import asyncio
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from services.moru_v2.meta import OfficialMetaProvider
from services.moru_v2.domain import Blocked


@pytest.mark.parametrize('platform', ['threads', 'instagram'])
def test_official_exchange_contract(platform):
    calls = []
    scope = 'threads_basic' if platform == 'threads' else 'instagram_business_basic'
    def handle(request):
        calls.append(request)
        if request.url.path == '/oauth/access_token':
            assert request.method == 'POST'
            body = parse_qs(request.content.decode())
            assert body['client_secret'] == ['synthetic-app-secret']
            assert body['code'] == ['synthetic-code']
            assert body['redirect_uri'] == [f'https://moru.test/connections/{platform}/callback']
            assert 'client_secret' not in str(request.url)
            grant = {'access_token': 'synthetic-short', 'user_id': '123', 'permissions': scope}
            return httpx.Response(200, json={'data': [grant]} if platform == 'instagram' else grant)
        if request.url.path == '/access_token':
            assert request.url.params['access_token'] == 'synthetic-short'
            return httpx.Response(200, json={'access_token': 'synthetic-long', 'token_type': 'bearer', 'expires_in': 5184000})
        assert request.headers['Authorization'] == 'Bearer synthetic-long'
        key = 'id' if platform == 'threads' else 'user_id'
        return httpx.Response(200, json={key: '123'})
    provider = OfficialMetaProvider(platform, client_id='1234', client_secret='synthetic-app-secret',
        callback_uri=f'https://moru.test/connections/{platform}/callback',
        transport=httpx.MockTransport(handle), clock=lambda: 1800000000)
    auth = parse_qs(urlsplit(provider.authorize_url('opaque-state')).query)
    assert auth['scope'] == [scope] and auth['state'] == ['opaque-state']
    assert 'secret' not in provider.authorize_url('opaque-state')
    grant = asyncio.run(provider.exchange('synthetic-code'))
    assert grant.account_id == '123' and grant.access_token == 'synthetic-long'
    assert grant.expires_at == 1805184000 and grant.scopes == (scope,)
    assert len(calls) == 3
    assert all('/media' not in r.url.path for r in calls)


@pytest.mark.parametrize('case', ['redirect', 'denied', 'ambiguous', 'missing-scope', 'identity-mismatch', 'bad-expiry'])
def test_provider_errors_fail_closed_and_never_retry(case):
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == '/oauth/access_token':
            if case == 'redirect':
                return httpx.Response(302, headers={'location': 'https://evil.test/token'})
            if case == 'denied':
                return httpx.Response(400, json={'error': 'SECRET-MUST-NOT-LEAK'})
            item = {'access_token': 'short', 'user_id': '123', 'permissions': 'instagram_business_basic'}
            if case == 'missing-scope':
                item['permissions'] = ''
            return httpx.Response(200, json={'data': [item, item] if case == 'ambiguous' else [item]})
        if request.url.path == '/access_token':
            return httpx.Response(200, json={'access_token': 'long', 'token_type': 'bearer',
                                           'expires_in': True if case == 'bad-expiry' else 100})
        return httpx.Response(200, json={'user_id': '999'})
    provider = OfficialMetaProvider('instagram', client_id='1234', client_secret='SECRET-MUST-NOT-LEAK',
        callback_uri='https://moru.test/connections/instagram/callback', transport=httpx.MockTransport(handle))
    with pytest.raises(Blocked, match='^provider_exchange_failed$'):
        asyncio.run(provider.exchange('code'))
    assert len([r for r in calls if r.url.path == '/oauth/access_token']) == 1
    assert all(r.url.host != 'evil.test' for r in calls)


def test_no_x_or_unregistered_callback_shape():
    for platform, callback in [('x', 'https://moru.test/connections/x/callback'),
                               ('threads', 'http://moru.test/connections/threads/callback'),
                               ('threads', 'https://moru.test/connections/instagram/callback')]:
        with pytest.raises(ValueError):
            OfficialMetaProvider(platform, client_id='123', client_secret='secret', callback_uri=callback)
