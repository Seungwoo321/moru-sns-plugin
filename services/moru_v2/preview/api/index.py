"""Synthetic-only Vercel probe. No login, database, Meta calls or live credentials."""
import logging
import os
import re

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from privacy import CallbackPrivacyMiddleware, callback_query, sensitive_http

CANARY = re.compile(r'^MORU_CANARY_[A-Za-z0-9_-]{1,80}$')
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
           'X-Robots-Tag': 'noindex', 'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"}


def reply(value, status=200):
    return JSONResponse(value, status_code=status, headers=HEADERS)


async def health(request):
    return reply({'mode': 'synthetic-preview', 'live_accounts': False})


async def callback(request):
    params = callback_query.get()
    if (params is None or set(params) != {'code', 'state', 'mode'} or
            any(len(params.getlist(k)) != 1 for k in params) or
            not all(CANARY.fullmatch(params[k]) for k in ('code', 'state')) or
            params['mode'] not in ('ok', 'error')):
        return reply({'error': 'synthetic_canary_required'}, 400)

    def exchange(req):
        # Exercise library logging with a synthetic query, without any network.
        if params['mode'] == 'error':
            try:
                raise RuntimeError(params['code'])
            except RuntimeError:
                logging.getLogger('httpcore.connection').warning('probe %s', req.url, exc_info=True)
            raise RuntimeError('synthetic_exchange_failed')
        return httpx.Response(200, json={'fixture': True})

    try:
        with sensitive_http():
            async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as client:
                await client.get('https://fixture.invalid/token', params={'code': params['code']})
        logging.getLogger('moru.preview').warning('synthetic_callback_completed')
        return reply({'status': 'synthetic_callback_completed'})
    except Exception:
        logging.getLogger('moru.preview').warning('synthetic_callback_failed')
        return reply({'error': 'synthetic_callback_failed'}, 502)


routes = [Route('/health', health)] + [Route(p, callback) for p in (
    '/auth/callback', '/connections/threads/callback', '/connections/instagram/callback')]
protected_app = CallbackPrivacyMiddleware(Starlette(routes=routes))


async def app(scope, receive, send):
    # First Vercel deployment may be production despite a requested preview target.
    # Runtime refusal is a second guard; it is NOT a replacement for platform SSO.
    if scope['type'] == 'http' and os.environ.get('VERCEL_ENV') != 'preview':
        return await reply({'error': 'preview_environment_required'}, 503)(scope, receive, send)
    return await protected_app(scope, receive, send)
