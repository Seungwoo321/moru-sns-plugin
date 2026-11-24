"""Same-origin UI shell; no login issuer is invented or enabled by this module."""
import json
from pathlib import Path
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

ROOT = Path(__file__).parent / 'web'


def routes(*, issuer=None, identity_ready=False, providers=()):
    if type(identity_ready) is not bool:
        raise ValueError('Identity readiness must be an explicit boolean')
    if identity_ready and not issuer:
        raise ValueError('Verified issuer and live session bridge required')

    async def page(request):
        name = {'/auth/sign-in': 'login', '/auth/consent': 'consent', '/account': 'account'}[request.url.path]
        config = json.dumps(dict(page=name, issuer=issuer, identity_ready=identity_ready,
                                 providers=sorted(providers))).replace('<', '\\u003c')
        return HTMLResponse('''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Moru 계정 연결</title>
<link rel="stylesheet" href="/assets/moru.css"><body><main id="app" aria-live="polite"><h1>Moru 계정 연결</h1><p>연결 상태를 확인하고 있습니다.</p></main>
<script type="application/json" id="moru-config">''' + config + '''</script>
<script type="module" src="/assets/moru.js"></script></body></html>''')

    async def bundle(request):
        path = ROOT / 'dist' / 'app.js'
        if not path.is_file():
            return Response('Moru UI build unavailable', status_code=503)
        return Response(path.read_bytes(), media_type='application/javascript')
    async def stylesheet(request):
        return Response((ROOT / 'style.css').read_bytes(), media_type='text/css')
    return [Route(path, page) for path in ('/account', '/auth/sign-in', '/auth/consent')] + [Route('/assets/moru.js', bundle), Route('/assets/moru.css', stylesheet)]
