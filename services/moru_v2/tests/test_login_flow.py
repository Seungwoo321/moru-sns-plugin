"""Full RP code flow against Authlib's test-only authorization server.

No network, real user, mail, Meta app or production issuer is contacted.
"""
import base64
import json
import logging
import os
import secrets
import time
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs, urlencode

import httpx
import jwt
import pytest
from flask import Flask, request as flask_request, jsonify
from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import ClientMixin, grants
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oidc.core import AuthorizationCodeMixin, UserInfo
from authlib.oidc.core.grants import OpenIDCode
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from sqlalchemy import create_engine, select, update
from starlette.testclient import TestClient

from services.moru_v2.session import LoginConfig, ServerSessions, build_app, sessions
from services.moru_v2.store import Store, Vault, metadata
from services.moru_v2.domain import Grant, Principal, digest

ORIGIN='https://moru.test'
ISSUER='https://issuer.test'
CLIENT='moru-browser'
SECRET='fixture-client-secret'
CALLBACK=ORIGIN+'/auth/callback'


class Client(ClientMixin):
    client_id=CLIENT
    def get_client_id(self): return CLIENT
    def get_default_redirect_uri(self): return CALLBACK
    def get_allowed_scope(self, scope): return ' '.join(s for s in scope.split() if s in ('openid','profile'))
    def check_redirect_uri(self, uri): return uri == CALLBACK
    def check_client_secret(self, secret): return secrets.compare_digest(secret, SECRET)
    def check_endpoint_auth_method(self, method, endpoint): return method == 'client_secret_basic'
    def check_response_type(self, value): return value == 'code'
    def check_grant_type(self, value): return value == 'authorization_code'


class Code(AuthorizationCodeMixin):
    def __init__(self, code, request):
        self.code, self.user = code, request.user
        self.redirect_uri, self.scope = request.payload.redirect_uri, request.payload.scope
        self.nonce = request.payload.data.get('nonce')
        self.code_challenge = request.payload.data.get('code_challenge')
        self.code_challenge_method = request.payload.data.get('code_challenge_method')
    def get_redirect_uri(self): return self.redirect_uri
    def get_scope(self): return self.scope
    def get_nonce(self): return self.nonce
    def get_auth_time(self): return int(time.time())


@pytest.fixture
def flow(tmp_path):
    codes, calls = {}, []
    controls = {'user':'alice','deny':False,'tamper':None}
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(use='sig',alg='RS256')
    client=Client()
    app=Flask(__name__)
    server=AuthorizationServer(app,query_client=lambda value:client if value==CLIENT else None,
                               save_token=lambda token,request:None)
    class CodeGrant(grants.AuthorizationCodeGrant):
        def save_authorization_code(self, code, request): codes[code]=Code(code,request)
        def query_authorization_code(self, code, client): return codes.get(code)
        def delete_authorization_code(self, code): codes.pop(code.code,None)
        def authenticate_user(self, code): return code.user
    class OIDC(OpenIDCode):
        def exists_nonce(self, nonce, request): return any(c.nonce==nonce for c in codes.values())
        def resolve_client_private_key(self, client):
            from joserfc.jwk import RSAKey
            return RSAKey.import_key(pem)
        def get_client_algorithm(self, client): return 'RS256'
        def get_client_claims(self, client): return dict(iss=ISSUER,exp=int(time.time())+3600)
        def generate_user_info(self, user, scope): return UserInfo(sub=user.id)
    server.register_grant(CodeGrant,[CodeChallenge(required=True),OIDC(require_nonce=True)])
    @app.route('/authorize')
    def authorize():
        user=None if controls['deny'] else SimpleNamespace(id=controls['user'])
        return server.create_authorization_response(grant_user=user)
    @app.route('/token',methods=['POST'])
    def token():
        calls.append(dict(flask_request.form))
        return server.create_token_response()
    @app.route('/keys')
    def keys(): return jsonify(keys=[public])
    issuer_client=app.test_client()
    def transport(req):
        path=req.url.raw_path.decode()
        data=req.content
        if controls['tamper']=='verifier' and req.url.path=='/token':
            form=parse_qs(data.decode()); form['code_verifier']=['wrong-verifier'];data=urlencode({k:v[0] for k,v in form.items()}).encode()
        response=issuer_client.open(path,method=req.method,base_url=ISSUER,
            headers=dict(req.headers),data=data)
        body=response.data
        if req.url.path=='/token' and response.status_code==200 and controls['tamper'] in ('nonce','issuer','audience','signature'):
            payload=json.loads(body)
            claims=jwt.decode(payload['id_token'],options={'verify_signature':False})
            target={'nonce':'nonce','issuer':'iss','audience':'aud'}.get(controls['tamper'])
            if target: claims[target]='wrong-value'
            signing_key=key if target else rsa.generate_private_key(public_exponent=65537,key_size=2048)
            payload['id_token']=jwt.encode(claims,signing_key,algorithm='RS256')
            body=json.dumps(payload).encode()
        return httpx.Response(response.status_code,content=body,headers={'content-type':'application/json'})
    db_url=os.environ.get('MORU_V2_TEST_DATABASE_URL',f'sqlite:///{tmp_path}/login.sqlite')
    engine=create_engine(db_url)
    metadata.create_all(engine)
    store=Store(engine,Vault({'fixture':secrets.token_bytes(32)},'fixture'))
    md=dict(issuer=ISSUER,authorization_endpoint=ISSUER+'/authorize',token_endpoint=ISSUER+'/token',
        jwks_uri=ISSUER+'/keys',scopes_supported=['openid','profile'],response_types_supported=['code'],
        code_challenge_methods_supported=['S256'],token_endpoint_auth_methods_supported=['client_secret_basic'],
        id_token_signing_alg_values_supported=['RS256'])
    config=LoginConfig(ORIGIN,ISSUER,CLIENT,SECRET,secrets.token_urlsafe(40),ORIGIN+'/mcp',md)
    application=build_app(store,config,providers={},transport=httpx.MockTransport(transport))
    with TestClient(application,base_url=ORIGIN) as browser:
        yield SimpleNamespace(browser=browser,issuer=issuer_client,controls=controls,calls=calls,
                              store=store,config=config,codes=codes)
    engine.dispose()


def authorize(flow):
    response=flow.browser.get('/login',follow_redirects=False)
    assert response.status_code==302,response.text
    location=response.headers['location']
    params=parse_qs(urlsplit(location).query)
    assert params['code_challenge_method']==['S256']
    assert params['resource']==[ORIGIN+'/mcp']
    assert params['nonce'] and params['state'] and params['code_challenge']
    approved=flow.issuer.get(urlsplit(location).path+'?'+urlsplit(location).query,base_url=ISSUER)
    assert approved.status_code==302,approved.text
    return approved.headers['Location']


def test_full_login_reload_tenant_isolation_and_local_logout(flow):
    assert flow.browser.get('/connections').status_code==401
    callback=authorize(flow)
    response=flow.browser.get(callback,follow_redirects=False)
    assert response.status_code==303,response.text
    assert flow.calls[0]['resource']==ORIGIN+'/mcp'
    assert flow.calls[0]['code_verifier']
    assert flow.browser.get('/connections').status_code==200
    cookie=flow.browser.cookies.get('__Host-moru-session')
    data=json.loads(base64.b64decode(cookie.split('.')[0]))
    assert set(data)=={'sid'}  # No code, verifier, token, nonce or user data retained.
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'secure' in response.headers['set-cookie'].lower()
    assert 'samesite=lax' in response.headers['set-cookie'].lower()
    with flow.store.engine.connect() as db:
        rows=db.execute(select(sessions).where(sessions.c.id_hash==digest(data['sid']))).mappings().all()
    assert len(rows)==1 and rows[0]['subject']=='alice'
    principal=Principal(ISSUER,'alice',data['sid'])
    flow.store.disconnect(principal.owner,'threads')
    state=flow.store.begin(principal,'threads')
    generation=flow.store.consume(principal,'threads',state)
    flow.store.finish(principal.owner,'threads',generation,Grant('alice-meta','fixture-token',int(time.time())+600,['threads_basic']))
    old_context=flow.browser.get('/connections').json()['context']
    assert 'alice-meta' in flow.browser.get('/connections').text
    flow.controls['user']='bob'
    second=authorize(flow)
    assert flow.browser.get(second,follow_redirects=False).status_code==303
    assert 'alice-meta' not in flow.browser.get('/connections').text
    assert flow.browser.post('/connections/threads/disconnect',headers={'Origin':ORIGIN,'X-Moru-Context':old_context}).status_code==409
    # Alice's saved session cookie is revoked by account switching.
    assert flow.browser.get('/connections',headers={'Cookie':'__Host-moru-session='+cookie}).status_code==401
    current=flow.browser.cookies.get('__Host-moru-session')
    assert flow.browser.post('/logout',headers={'Origin':ORIGIN}).json()['issuer_session']=='unchanged'
    assert flow.browser.get('/connections',headers={'Cookie':'__Host-moru-session='+current}).status_code==401


@pytest.mark.parametrize('tamper',['verifier','nonce','issuer','audience','signature'])
def test_protocol_failures_never_create_server_session(flow,tamper):
    callback=authorize(flow)
    flow.controls['tamper']=tamper
    response=flow.browser.get(callback,follow_redirects=False)
    assert response.status_code==400,response.text
    assert flow.browser.get('/connections').status_code==401
    assert 'wrong-value' not in response.text


def test_state_denial_and_code_replay(flow):
    callback=authorize(flow)
    params=parse_qs(urlsplit(callback).query)
    params['state']=['bad-state']
    bad=CALLBACK+'?'+urlencode({k:v[0] for k,v in params.items()})
    assert flow.browser.get(bad,follow_redirects=False).status_code==400
    assert flow.calls==[]
    flow.controls['deny']=True
    assert flow.browser.get(authorize(flow),follow_redirects=False).status_code==400
    assert flow.calls==[]
    flow.controls['deny']=False
    callback=authorize(flow)
    assert flow.browser.get(callback,follow_redirects=False).status_code==303
    assert flow.browser.get(callback,follow_redirects=False).status_code==400
    assert len(flow.calls)==1
    # The real Authlib issuer also rejects replay of the already redeemed code.
    response=flow.issuer.post('/token',base_url=ISSUER,data=flow.calls[0],
        headers={'Authorization':'Basic '+base64.b64encode((CLIENT+':'+SECRET).encode()).decode()})
    assert response.status_code==400


def test_cookie_tampering_expiry_and_logout_origin(flow):
    assert flow.browser.get(authorize(flow),follow_redirects=False).status_code==303
    cookie=flow.browser.cookies.get('__Host-moru-session')
    assert flow.browser.post('/logout',headers={'Origin':'https://other.test'}).status_code==403
    assert flow.browser.get('/connections').status_code==200
    assert flow.browser.get('/connections',headers={'Cookie':'__Host-moru-session=x'+cookie}).status_code==401
    sid=json.loads(base64.b64decode(cookie.split('.')[0]))['sid']
    with flow.store.engine.begin() as db:
        db.execute(update(sessions).where(sessions.c.id_hash==digest(sid)).values(expires_at=0))
    assert flow.browser.get('/connections',headers={'Cookie':'__Host-moru-session='+cookie}).status_code==401


def test_oidc_callback_logs_do_not_contain_credentials(flow,caplog):
    caplog.set_level(logging.DEBUG)
    callback=authorize(flow)
    flow.controls['tamper']='signature'
    caplog.clear()
    assert flow.browser.get(callback,follow_redirects=False).status_code==400
    # TestClient itself logs the inbound URL outside the service; exclude that client
    # record, just as platform access logs require their own release gate.
    records=[r for r in caplog.records if r.name.startswith('authlib') or r.name.startswith('httpcore')]
    assert all('access_token' not in r.getMessage() and 'id_token' not in r.getMessage() for r in records)
