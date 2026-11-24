"""Official, connection-only Meta OAuth adapter. No publishing or app mutations.

Only basic identity scope is requested. Hosts must disable HTTP request tracing
and URL/body logging: Meta's documented long-token exchange uses secret queries.
No retry is made for authorization-code exchange or ambiguous provider responses.
"""
import time
from urllib.parse import urlencode, urlsplit
import httpx
from .domain import Blocked, Grant, PLATFORMS
from .privacy import sensitive_http


class OfficialMetaProvider:
    def __init__(self, platform, *, client_id, client_secret, callback_uri,
                 transport=None, clock=time.time):
        if platform not in PLATFORMS:
            raise ValueError('Unsupported provider')
        url = urlsplit(callback_uri)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or
                url.query or url.fragment or '*' in callback_uri or
                url.path != f'/connections/{platform}/callback'):
            raise ValueError('Exact registered HTTPS callback required')
        if not client_id.isascii() or not client_id.isdigit() or not client_secret:
            raise ValueError('Dedicated platform application credentials required')
        self.platform, self.client_id, self._secret = platform, client_id, client_secret
        self.callback, self.transport, self.clock = callback_uri, transport, clock
        self.scope = 'threads_basic' if platform == 'threads' else 'instagram_business_basic'
        self.graph = 'https://graph.threads.com' if platform == 'threads' else 'https://graph.instagram.com'

    def authorize_url(self, state):
        if not state:
            raise ValueError('OAuth state is required')
        endpoint = ('https://threads.com/oauth/authorize' if self.platform == 'threads'
                    else 'https://www.instagram.com/oauth/authorize')
        return endpoint + '?' + urlencode(dict(client_id=self.client_id, redirect_uri=self.callback,
            response_type='code', scope=self.scope, state=state))

    async def exchange(self, code):
        try:
            with sensitive_http():
                return await self._exchange(code)
        except Exception:
            # HTTP error objects often contain query tokens, response bodies or secrets.
            raise Blocked('provider_exchange_failed') from None

    async def _exchange(self, code):
        if not isinstance(code, str) or not code or len(code) > 4096:
            raise ValueError('Invalid code')
        # Reject redirects, proxy environment inheritance, and automatic retries.
        async with httpx.AsyncClient(transport=self.transport, timeout=15,
                                     follow_redirects=False, trust_env=False) as client:
            async def call(method, url, **kwargs):
                response = await client.request(method, url, **kwargs)
                if response.status_code != 200 or len(response.content) > 65536:
                    raise ValueError('Rejected provider response')
                result = response.json()
                if not isinstance(result, dict) or 'error' in result:
                    raise ValueError('Invalid provider response')
                return result

            token_url = (self.graph + '/oauth/access_token' if self.platform == 'threads'
                         else 'https://api.instagram.com/oauth/access_token')
            short = await call('POST', token_url, data=dict(client_id=self.client_id,
                client_secret=self._secret, redirect_uri=self.callback,
                grant_type='authorization_code', code=code))
            if 'data' in short:
                if (self.platform != 'instagram' or 'access_token' in short or
                        not isinstance(short['data'], list) or len(short['data']) != 1 or
                        not isinstance(short['data'][0], dict)):
                    raise ValueError('Ambiguous token response')
                short = short['data'][0]
            token = short.get('access_token')
            account = str(short.get('user_id', ''))
            if not isinstance(token, str) or not token or not account.isascii() or not account.isdigit():
                raise ValueError('Missing token identity')
            if self.platform == 'instagram':
                granted = short.get('permissions', '')
                if not isinstance(granted, str) or self.scope not in granted.split(','):
                    raise ValueError('Required permission not granted')
            long = await call('GET', self.graph + '/access_token', params=dict(
                grant_type='th_exchange_token' if self.platform == 'threads' else 'ig_exchange_token',
                client_secret=self._secret, access_token=token))
            token, ttl = long.get('access_token'), long.get('expires_in')
            if (not isinstance(token, str) or not token or type(ttl) is not int or
                    not 0 < ttl <= 60 * 86400 or long.get('token_type', '').lower() != 'bearer'):
                raise ValueError('Invalid long-lived token')
            # Basic /me success verifies required basic access and binds token to identity.
            key = 'id' if self.platform == 'threads' else 'user_id'
            version = 'v1.0' if self.platform == 'threads' else 'v27.0'
            identity = await call('GET', f'{self.graph}/{version}/me', params={'fields': key},
                                  headers={'Authorization': f'Bearer {token}'})
            if str(identity.get(key, '')) != account:
                raise ValueError('Token account mismatch')
            return Grant(account, token, int(self.clock()) + ttl, (self.scope,))
