"""Standard OIDC relying party + revocable server sessions, using Authlib.

Only test fixtures implement an issuer. Live issuer/client registration is external.
"""
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import Table, Column, String, BigInteger, insert, select, update
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from .app import create_app
from .domain import Principal, digest
from .issuer import IssuerVerifier, https
from .privacy import callback_query, sensitive_http
from .store import metadata

sessions = Table('moru_v2_sessions', metadata,
    Column('id_hash', String(64), primary_key=True),
    Column('issuer', String(2048), nullable=False),
    Column('subject', String(512), nullable=False),
    Column('expires_at', BigInteger, nullable=False))


@dataclass(frozen=True, repr=False)
class LoginConfig:
    origin: str
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    cookie_secret: str = field(repr=False)
    resource: str
    metadata: dict

    def validate(self):
        https(self.origin)
        if urlsplit(self.origin).path or not self.client_secret or len(self.cookie_secret) < 32:
            raise ValueError('Exact service origin and dedicated client/cookie secrets required')
        algorithms = set(self.metadata.get('id_token_signing_alg_values_supported', []))
        if not algorithms or algorithms - {'RS256', 'ES256', 'EdDSA'}:
            raise ValueError('Explicit asymmetric ID token algorithm policy required')
        # Policy preflight only; Authlib performs the actual RP protocol.
        IssuerVerifier(issuer=self.issuer, resource=self.resource, metadata=self.metadata,
                       clients={self.client_id}, scopes={'openid', 'profile'})
        if 'client_secret_basic' not in self.metadata['token_endpoint_auth_methods_supported']:
            raise ValueError('Registered confidential RP with client_secret_basic required')


class ServerSessions:
    def __init__(self, engine, issuer, clock=time.time):
        self.engine, self.issuer, self.clock = engine, issuer, clock

    def issue(self, subject, expiry):
        sid = secrets.token_urlsafe(32)
        with self.engine.begin() as db:
            db.execute(insert(sessions).values(id_hash=digest(sid),issuer=self.issuer,
                subject=subject, expires_at=min(int(expiry), int(self.clock()) + 3600)))
        return sid

    async def verify(self, request):
        sid = request.session.get('sid')
        if not isinstance(sid, str) or len(sid) > 128:
            return None
        with self.engine.connect() as db:
            row = db.execute(select(sessions).where(sessions.c.id_hash == digest(sid),
                sessions.c.issuer == self.issuer, sessions.c.expires_at > int(self.clock()))).mappings().one_or_none()
        return Principal(self.issuer,row['subject'],sid) if row else None

    def revoke(self, sid):
        if isinstance(sid, str):
            with self.engine.begin() as db:
                db.execute(update(sessions).where(sessions.c.id_hash == digest(sid),
                    sessions.c.issuer == self.issuer).values(expires_at=0))


def build_app(store, config: LoginConfig, *, providers, transport=None):
    """Real ASGI entrypoint factory. No schema creation, env fallback or listener."""
    config.validate()
    session_store = ServerSessions(store.engine, config.issuer)
    oauth = OAuth()
    options = dict(scope='openid profile',code_challenge_method='S256',
                   token_endpoint_auth_method='client_secret_basic',timeout=10,trust_env=False)
    if transport is not None:
        options['transport'] = transport
    client = oauth.register('moru',client_id=config.client_id,client_secret=config.client_secret,
        client_kwargs=options, **config.metadata)

    async def login(request):
        try:
            # End any prior local session before starting an account switch.
            session_store.revoke(request.session.get('sid'))
            request.session.clear()
            with sensitive_http():
                return await client.authorize_redirect(request,config.origin+'/auth/callback',resource=config.resource)
        except Exception:
            request.session.clear()
            return failure(503)

    def failure(status=400):
        return HTMLResponse('<h1>로그인을 완료하지 못했습니다</h1><p>다시 로그인해 주세요.</p>'
                            '<a href="/login">로그인 다시 시작</a>',status_code=status)

    async def callback(request):
        try:
            params = callback_query.get()
            if params is None or any(len(params.getlist(k)) != 1 for k in ('state',)):
                raise ValueError('Missing state')
            if any(len(params.getlist(k)) > 1 for k in ('code','error','iss')):
                raise ValueError('Ambiguous callback')
            if config.metadata.get('authorization_response_iss_parameter_supported'):
                if params.get('iss') != config.issuer:
                    raise ValueError('Issuer mix-up')
            elif params.get('iss') and params['iss'] != config.issuer:
                raise ValueError('Issuer mix-up')
            # Restore query only for the library's private Request, never shared access-log scope.
            private_scope = dict(request.scope, query_string=str(params).encode())
            with sensitive_http():
                token = await client.authorize_access_token(Request(private_scope),resource=config.resource,
                    claims_options={'iss': {'essential':True,'value':config.issuer}},leeway=0)
            user = token.get('userinfo')
            if not user or not isinstance(user.get('sub'),str) or not user['sub'] or len(user['sub']) > 512:
                raise ValueError('Verified OIDC subject required')
            sid = session_store.issue(user['sub'],user['exp'])
            # Never retain OAuth/ID/refresh tokens in cookies or session rows.
            request.session.clear()
            request.session['sid'] = sid
            return RedirectResponse('/account',status_code=303)
        except Exception:
            session_store.revoke(request.session.get('sid'))
            request.session.clear()
            return failure()

    async def logout(request):
        if request.headers.get('origin') != config.origin:
            return JSONResponse({'error':'invalid_origin'},status_code=403)
        session_store.revoke(request.session.get('sid'))
        request.session.clear()
        return JSONResponse({'status':'signed_out','issuer_session':'unchanged'})

    app = create_app(store,session_verifier=session_store.verify,providers=providers,origin=config.origin,
        issuer=config.issuer,identity_ready=True,additional_routes=[Route('/login',login),
            Route('/auth/callback',callback),Route('/logout',logout,methods=['POST'])])
    return SessionMiddleware(app,secret_key=config.cookie_secret,session_cookie='__Host-moru-session',
        max_age=3600,same_site='lax',https_only=True,path='/')
