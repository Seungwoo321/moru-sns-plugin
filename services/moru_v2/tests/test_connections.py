import asyncio
import json
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import create_engine, select
from starlette.testclient import TestClient
from mcp.server.auth.provider import AccessToken

from services.moru_v2.app import create_app
from services.moru_v2.domain import Principal, Grant, Blocked
from services.moru_v2.store import Store, Vault, metadata, connections
from services.moru_v2.mcp_app import create_server, ResourceVerifier

ORIGIN = 'https://moru.test'
ISSUER = 'https://identity.test'


class Provider:
    def __init__(self):
        self.calls = 0
        self.error = False
        self.hook = None

    def authorize_url(self, state):
        return f'https://provider.test/authorize?state={state}'

    async def exchange(self, code):
        self.calls += 1
        if self.hook:
            self.hook()
        if self.error:
            raise RuntimeError('private-provider-token')
        return Grant('12345', 'private-meta-token', 2000000000, ('basic',))


@pytest.fixture
def setup(tmp_path):
    # CI is offline by default. Explicit isolated Neon development URL enables SQL integration.
    url = os.environ.get('MORU_V2_TEST_DATABASE_URL', f'sqlite:///{tmp_path}/test.sqlite')
    engine = create_engine(url)
    metadata.create_all(engine)  # test/development provisioning only, never app startup
    store = Store(engine, Vault({'test-key': secrets.token_bytes(32)}, 'test-key'), clock=lambda: 1800000000)
    ident = secrets.token_hex(12)
    alice = Principal(ISSUER, f'alice-{ident}', 'session-a')
    bob = Principal(ISSUER, f'bob-{ident}', 'session-b')
    identities = {'alice': alice, 'bob': bob,
                  'alice-new-session': replace(alice, session_id='new-session')}

    async def verify(request):
        return identities.get(request.cookies.get('test-session'))

    provider = Provider()
    app = create_app(store, session_verifier=verify,
                     providers={'threads': provider, 'instagram': provider}, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        yield store, client, provider, alice, bob
    engine.dispose()


def request(client, method, path, who='alice', **kwargs):
    headers = {'Cookie': f'test-session={who}', 'Origin': ORIGIN}
    headers.update(kwargs.pop('headers', {}))
    return client.request(method, path, headers=headers, **kwargs)


def start(client, platform='threads', who='alice'):
    response = request(client, 'POST', f'/connections/{platform}/start', who)
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()['authorization_url']).query)['state'][0]


def callback(client, state, platform='threads', who='alice', **params):
    return request(client, 'GET', f'/connections/{platform}/callback', who,
                   params={'state': state, 'code': 'test-code', **params})


@pytest.mark.parametrize('platform', ['threads', 'instagram'])
def test_http_connect_isolated_encrypted_disconnect(setup, platform):
    store, client, provider, alice, bob = setup
    state = start(client, platform)
    assert callback(client, state, platform).status_code == 200
    assert provider.calls == 1
    assert store.list(bob.owner) == []
    result = request(client, 'GET', '/connections')
    assert result.json()['connections'][0]['account_id'] == '12345'
    assert 'token' not in result.text
    with store.engine.connect() as db:
        row = dict(db.execute(select(connections).where(connections.c.owner == alice.owner)).mappings().one())
    assert b'private-meta-token' not in row['ciphertext']
    assert store.vault.open(row) == 'private-meta-token'
    with pytest.raises(InvalidTag):
        store.vault.open(dict(row, owner=bob.owner))
    assert request(client, 'POST', f'/connections/{platform}/start').status_code == 400
    assert callback(client, state, platform).status_code == 400  # replay: no exchange
    assert provider.calls == 1
    request(client, 'POST', f'/connections/{platform}/disconnect', who='bob')
    assert store.list(alice.owner)[0]['status'] == 'connected'
    reply = request(client, 'POST', f'/connections/{platform}/disconnect')
    assert reply.json() == {'status': 'disconnected', 'provider_revocation': 'not_attempted'}
    with store.engine.connect() as db:
        row = db.execute(select(connections).where(connections.c.owner == alice.owner)).mappings().one()
        assert row['ciphertext'] is None and row['account_id'] is None and row['state_hash'] is None
    assert request(client, 'POST', f'/connections/{platform}/disconnect').status_code == 200
    assert callback(client, start(client, platform), platform).status_code == 200


def test_state_owner_session_platform_expiry_and_replay(setup):
    store, client, provider, alice, _ = setup
    state = start(client)
    assert callback(client, state, who='bob').status_code == 400
    assert callback(client, state, who='alice-new-session').status_code == 400
    assert callback(client, state, platform='instagram').status_code == 400
    assert callback(client, 'bogus').status_code == 400
    assert provider.calls == 0
    store.clock = lambda: 1800000600
    assert callback(client, state).status_code == 400
    assert provider.calls == 0


def test_cancel_and_error_are_consumed_without_leaking(setup):
    store, client, provider, alice, _ = setup
    state = start(client)
    reply = callback(client, state, error='access_denied')
    assert reply.status_code == 400 and provider.calls == 0
    assert callback(client, state).status_code == 400
    assert store.list(alice.owner)[0]['status'] == 'disconnected'
    state = start(client)
    provider.error = True
    reply = callback(client, state)
    assert reply.status_code == 400 and 'private-provider-token' not in reply.text
    assert callback(client, state).status_code == 400
    assert provider.calls == 1


def test_disconnect_during_exchange_cannot_resurrect(setup):
    store, client, provider, alice, _ = setup
    state = start(client)
    provider.hook = lambda: store.disconnect(alice.owner, 'threads')
    assert callback(client, state).status_code == 400
    assert store.list(alice.owner)[0]['status'] == 'disconnected'
    assert callback(client, state).status_code == 400


def test_new_attempt_supersedes_inflight_exchange(setup):
    store, client, provider, alice, _ = setup
    state = start(client)
    replacement = []
    provider.hook = lambda: replacement.append(store.begin(alice, 'threads'))
    assert callback(client, state).status_code == 400
    assert store.list(alice.owner)[0]['status'] == 'pending'
    provider.hook = None
    assert callback(client, replacement[0]).status_code == 200


def test_atomic_double_callback(setup):
    store, client, provider, alice, _ = setup
    state = start(client)
    barrier = threading.Barrier(2)
    def consume():
        barrier.wait()
        try:
            return store.consume(alice, 'threads', state)
        except Blocked:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sum(x is not None for x in results) == 1


def test_http_auth_csrf_unknown_platform_and_security_headers(setup):
    store, client, provider, _, _ = setup
    assert request(client, 'GET', '/connections', who='unknown').status_code == 401
    assert request(client, 'POST', '/connections/threads/start', headers={'Origin': 'https://evil.test'}).status_code == 403
    assert request(client, 'POST', '/connections/x/start').status_code == 400
    assert request(client, 'POST', '/connections/threads/start', headers={'Host': 'evil.test'}).status_code == 400
    response = request(client, 'GET', '/connections')
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert provider.calls == 0


def test_ambiguous_callback_does_not_consume_state(setup):
    _, client, provider, _, _ = setup
    state = start(client)
    response = request(client, 'GET', f'/connections/threads/callback?state={state}&state=bad&code=x')
    assert response.status_code == 400 and provider.calls == 0
    assert callback(client, state).status_code == 200


def test_bad_grant_and_expired_connection(setup):
    store, client, _, alice, _ = setup
    state = start(client)
    generation = store.consume(alice, 'threads', state)
    with pytest.raises(Blocked):
        store.finish(alice.owner, 'threads', generation, Grant('1', 'secret', 1, ('basic',)))
    store.finish(alice.owner, 'threads', generation, Grant('1', 'secret', 1800000001, ('basic',)))
    store.clock = lambda: 1800000002
    assert store.list(alice.owner)[0]['status'] == 'expired'


class OAuthVerifier:
    def __init__(self, subject):
        self.subject = subject
    async def verify_token(self, value):
        if value == 'invalid':
            return None
        return AccessToken(token=value, client_id='work', subject=self.subject,
            resource=ORIGIN + '/mcp', expires_at=2000000000,
            scopes=['moru:connections:read'] if value != 'unscoped' else [])


def test_mcp_http_boundary_discovery_and_tenant(setup):
    store, _, _, alice, bob = setup
    state = store.begin(alice, 'threads')
    generation = store.consume(alice, 'threads', state)
    store.finish(alice.owner, 'threads', generation, Grant('alice-account', 'secret', 2000000000, ('basic',)))
    verifier = OAuthVerifier(bob.subject)
    server = create_server(store, verifier=verifier, issuer=ISSUER,
        resource=ORIGIN + '/mcp', clients={'work'}, origins=[ORIGIN])
    with TestClient(server.streamable_http_app(), base_url=ORIGIN) as client:
        headers = {'Accept': 'application/json, text/event-stream', 'Origin': ORIGIN,
                   'MCP-Protocol-Version': '2025-06-18'}
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'moru_connections', 'arguments': {}}}
        assert client.post('/mcp', json=body, headers=headers).status_code == 401
        assert client.post('/mcp', json=body, headers=dict(headers, Authorization='Bearer unscoped')).status_code == 403
        reply = client.post('/mcp', json=body, headers=dict(headers, Authorization='Bearer valid'))
        assert reply.status_code == 200, reply.text
        assert 'alice-account' not in reply.text and 'secret' not in reply.text
        assert json.loads(reply.json()['result']['content'][0]['text'])['connections'] == []
        verifier.subject = alice.subject
        reply = client.post('/mcp', json=body, headers=dict(headers, Authorization='Bearer valid'))
        assert 'alice-account' in reply.text and 'secret' not in reply.text
        discovery = client.get('/.well-known/oauth-protected-resource/mcp')
        assert discovery.status_code == 200
        assert discovery.json()['authorization_servers'] == [ISSUER + '/']


def test_resource_verifier_rejects_wrong_audience_client_expiry():
    class Delegate:
        async def verify_token(self, token):
            return self.result
    delegate = Delegate()
    bound = ResourceVerifier(delegate, ORIGIN + '/mcp', {'work'})
    base = dict(token='opaque', client_id='work', subject='user', resource=ORIGIN + '/mcp',
                expires_at=2000000000, scopes=['moru:connections:read'])
    for changes in ({'resource': 'https://evil.test'}, {'client_id': 'other'},
                    {'expires_at': 1}, {'expires_at': None}, {'subject': ''}):
        delegate.result = AccessToken(**dict(base, **changes))
        assert asyncio.run(bound.verify_token('opaque')) is None


@pytest.mark.parametrize('platform', ['threads', 'instagram'])
def test_http_callback_with_official_adapter(setup, platform):
    import httpx
    from services.moru_v2.meta import OfficialMetaProvider
    store, _, _, alice, _ = setup
    scope = 'threads_basic' if platform == 'threads' else 'instagram_business_basic'
    calls = []
    def handle(req):
        calls.append(req)
        if req.url.path == '/oauth/access_token':
            data = {'access_token': 'short', 'user_id': '987', 'permissions': scope}
            return httpx.Response(200, json={'data': [data]} if platform == 'instagram' else data)
        if req.url.path == '/access_token':
            return httpx.Response(200, json={'access_token': 'long-secret', 'token_type': 'bearer', 'expires_in': 1000})
        return httpx.Response(200, json={'id': '987', 'user_id': '987'})
    provider = OfficialMetaProvider(platform, client_id='123', client_secret='app-secret',
        callback_uri=f'{ORIGIN}/connections/{platform}/callback', transport=httpx.MockTransport(handle),
        clock=store.clock)
    async def verify(req):
        return alice if req.cookies.get('test-session') == 'alice' else None
    app = create_app(store, session_verifier=verify, providers={platform: provider}, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        state = start(client, platform)
        assert callback(client, 'invalid', platform).status_code == 400
        assert calls == []
        assert callback(client, state, platform).status_code == 200
        result = request(client, 'GET', '/connections')
        assert result.json()['connections'][0]['account_id'] == '987'
        assert 'long-secret' not in result.text
        assert len(calls) == 3
        assert callback(client, state, platform).status_code == 400
        assert len(calls) == 3
        assert request(client, 'POST', f'/connections/{platform}/disconnect').status_code == 200
        assert store.list(alice.owner)[0]['status'] == 'disconnected'


def test_ui_shell_blocks_unconfigured_identity_and_sets_frame_protection(setup):
    _, client, _, _, _ = setup
    for path in ('/account','/auth/sign-in','/auth/consent'):
        response = client.get(path)
        assert response.status_code == 200
        assert '"identity_ready": false' in response.text
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert response.headers['cache-control'] == 'no-store'
        assert 'account_id' not in response.text


def test_stale_ui_context_cannot_mutate_new_session(setup):
    store, client, provider, alice, bob = setup
    context = request(client,'GET','/connections').json()['context']
    response = request(client,'POST','/connections/threads/start',who='bob',headers={'X-Moru-Context':context})
    assert response.status_code == 409
    assert store.list(bob.owner) == [] and provider.calls == 0
    response = request(client,'POST','/connections/threads/start',who='alice-new-session',headers={'X-Moru-Context':context})
    assert response.status_code == 409


def test_browser_callback_failure_is_safe_and_retryable(setup):
    _, client, provider, _, _ = setup
    state = start(client)
    provider.error = True
    response = request(client,'GET','/connections/threads/callback',
        params={'state':state,'code':'CALLBACK-CANARY'},headers={'Accept':'text/html'})
    assert response.status_code == 400
    assert '계정을 연결하지 못했습니다' in response.text
    assert 'href="/account"' in response.text
    assert 'CANARY' not in response.text and 'private-provider-token' not in response.text


def test_signed_issuer_tokens_at_actual_mcp_entrypoint(setup):
    import time
    import jwt
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from services.moru_v2.issuer import IssuerVerifier
    store, _, _, alice, bob = setup
    state = store.begin(alice, 'threads')
    store.finish(alice.owner,'threads',store.consume(alice,'threads',state),
                 Grant('ALICE-ONLY','private-token',2000000000,('basic',)))
    metadata = dict(issuer=ISSUER,code_challenge_methods_supported=['S256'],response_types_supported=['code'],
        scopes_supported=['profile'],token_endpoint_auth_methods_supported=['none'],
        authorization_endpoint=ISSUER+'/authorize',token_endpoint=ISSUER+'/token',jwks_uri=ISSUER+'/keys')
    verifier = IssuerVerifier(issuer=ISSUER,resource=ORIGIN+'/mcp',metadata=metadata,clients={'work'},scopes={'profile'})
    key = Ed25519PrivateKey.generate()
    verifier.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda token:SimpleNamespace(key=key.public_key()))
    server = create_server(store,verifier=verifier,issuer=ISSUER,resource=ORIGIN+'/mcp',clients={'work'},origins=[ORIGIN],read_scope='profile')
    with TestClient(server.streamable_http_app(),base_url=ORIGIN) as client:
        body = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'moru_connections','arguments':{}}}
        for subject,audience,issuer,status,visible in [(alice.subject,ORIGIN+'/mcp',ISSUER,200,True),
            (bob.subject,ORIGIN+'/mcp',ISSUER,200,False),(alice.subject,'https://other.test',ISSUER,401,False),
            (alice.subject,ORIGIN+'/mcp','https://other-branch.test',401,False)]:
            token=jwt.encode(dict(iss=issuer,aud=audience,sub=subject,client_id='work',scope='profile',exp=int(time.time())+300),key,algorithm='EdDSA')
            response=client.post('/mcp',json=body,headers={'Authorization':'Bearer '+token,
                'Accept':'application/json, text/event-stream','Origin':ORIGIN,'MCP-Protocol-Version':'2025-06-18'})
            assert response.status_code == status
            assert ('ALICE-ONLY' in response.text) == visible


def test_browser_callback_with_expired_session_shows_retry_without_exchange(setup):
    _,client,provider,_,_ = setup
    state=start(client)
    response=request(client,'GET','/connections/threads/callback',who='unknown',
        params={'state':state,'code':'EXPIRED-SESSION-CANARY'},headers={'Accept':'text/html'})
    assert response.status_code == 401 and provider.calls == 0
    assert 'href="/account"' in response.text and 'CANARY' not in response.text
