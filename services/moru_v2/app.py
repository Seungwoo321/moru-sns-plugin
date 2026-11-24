"""Account-management ASGI factory. Session verification is a required host dependency."""
from urllib.parse import urlsplit
import hmac
from html import escape
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from .domain import Blocked, PLATFORMS, digest
from .privacy import CallbackPrivacyMiddleware, callback_query
from .ui import routes as ui_routes
from .issuer import https


def create_app(store, *, session_verifier, providers, origin, issuer=None, identity_ready=False):
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or
            parsed.fragment or parsed.username or parsed.password or '*' in origin):
        raise ValueError('Exact HTTPS service origin required')
    if session_verifier is None or set(providers) - PLATFORMS:
        raise ValueError('Trusted session verifier and supported providers required')

    connect_origin = ''
    if identity_ready:
        parsed_issuer = urlsplit(https(issuer))
        connect_origin = ' ' + parsed_issuer.scheme + '://' + parsed_issuer.netloc

    def callback_response(request, success, status=400):
        if 'text/html' in request.headers.get('accept', ''):
            title = '계정이 연결되었습니다' if success else '계정을 연결하지 못했습니다'
            return HTMLResponse('<!doctype html><html lang="ko"><meta charset="utf-8"><title>Moru</title>'
                '<h1>' + escape(title) + '</h1><p>연결 상태를 확인하거나 다시 시도해 주세요.</p>'
                '<a href="/account">계정 연결로 돌아가기</a></html>', status_code=200 if success else status)
        return JSONResponse({'status': 'connected'})

    async def handle(request):
        try:
            principal = await session_verifier(request)
            if principal is None:
                if request.path_params.get('action') == 'callback' and 'text/html' in request.headers.get('accept', ''):
                    return callback_response(request, False, 401)
                return JSONResponse({'error': 'unauthenticated'}, status_code=401)
            owner = principal.owner
            if request.method == 'POST' and request.headers.get('origin') != origin:
                return JSONResponse({'error': 'invalid_origin'}, status_code=403)
            context = digest(owner, principal.session_id)
            if request.method == 'POST' and request.headers.get('x-moru-context') is not None:
                if not hmac.compare_digest(request.headers['x-moru-context'], context):
                    return JSONResponse({'error': 'session_changed'}, status_code=409)
            if request.url.path == '/connections':
                return JSONResponse({'connections': store.list(owner), 'context': context})
            platform = request.path_params['platform']
            if platform not in PLATFORMS:
                raise Blocked('unsupported_platform')
            action = request.path_params['action']
            if action == 'disconnect' and request.method == 'POST':
                return JSONResponse(store.disconnect(owner, platform))
            if platform not in providers:
                return JSONResponse({'error': 'provider_not_configured'}, status_code=503)
            provider = providers[platform]
            if action == 'start' and request.method == 'POST':
                state = store.begin(principal, platform)
                return JSONResponse({'authorization_url': provider.authorize_url(state)})
            if action == 'callback' and request.method == 'GET':
                query = callback_query.get()
                if query is None:
                    raise Blocked('callback_privacy_unavailable')
                if any(len(query.getlist(k)) > 1 for k in ('state', 'code', 'error')):
                    raise Blocked('ambiguous_callback')
                state = query.get('state', '')
                if not state or len(state) > 128:
                    raise Blocked('invalid_or_expired_state')
                generation = store.consume(principal, platform, state)
                try:
                    if query.get('error') or not query.get('code'):
                        raise Blocked('authorization_cancelled')
                    if len(query['code']) > 4096:
                        raise Blocked('invalid_code')
                    grant = await provider.exchange(query['code'])
                    store.finish(owner, platform, generation, grant)
                except Exception:
                    store.fail(owner, platform, generation)
                    raise Blocked('connection_not_completed') from None
                return callback_response(request, True)
            return JSONResponse({'error': 'not_found'}, status_code=404)
        except Blocked as error:
            if request.path_params.get('action') == 'callback' and 'text/html' in request.headers.get('accept', ''):
                return callback_response(request, False)
            return JSONResponse({'error': str(error)}, status_code=400)
        except Exception:
            if request.path_params.get('action') == 'callback' and 'text/html' in request.headers.get('accept', ''):
                return callback_response(request, False, 503)
            # Never reflect provider/database exceptions; these may contain credentials.
            return JSONResponse({'error': 'connection_service_unavailable'}, status_code=503)

    app = Starlette(routes=[Route('/connections', handle),
        Route('/connections/{platform}/{action}', handle, methods=['GET', 'POST'])] +
        ui_routes(issuer=issuer, identity_ready=identity_ready, providers=providers))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])

    async def private_response(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; connect-src 'self'" + connect_origin + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Frame-Options'] = 'DENY'
        return response
    app.add_middleware(BaseHTTPMiddleware, dispatch=private_response)
    return CallbackPrivacyMiddleware(app)
