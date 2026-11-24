"""Narrow credential redaction: only the current OAuth task's HTTP client logs."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from starlette.datastructures import QueryParams

_sensitive_http = ContextVar('moru_sensitive_http', default=False)
callback_query = ContextVar('moru_callback_query', default=None)


def install_http_redaction():
    """Preserve other record factories and unrelated application requests/logs.

    Record creation happens before handlers format messages or exceptions. A
    logger filter alone would miss descendant httpcore loggers and new handlers.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, '_moru_http_redaction', False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if _sensitive_http.get() and (record.name == 'httpx' or record.name.startswith('httpx.') or
                                     record.name == 'httpcore' or record.name.startswith('httpcore.')):
            record.msg = 'Moru OAuth HTTP activity (request details withheld)'
            record.args, record.exc_info, record.exc_text, record.stack_info = (), None, None, None
        return record
    factory._moru_http_redaction = True
    logging.setLogRecordFactory(factory)


@contextmanager
def sensitive_http():
    install_http_redaction()
    marker = _sensitive_http.set(True)
    try:
        yield
    finally:
        _sensitive_http.reset(marker)


class CallbackPrivacyMiddleware:
    """Remove callback query from the shared ASGI scope before app/access logging.

    Only the callback handler can read the original QueryParams via a task-local
    context. This cannot control a proxy that logged the URL before ASGI entry.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') not in (
                '/connections/threads/callback', '/connections/instagram/callback'):
            return await self.app(scope, receive, send)
        params = QueryParams(scope.get('query_string', b''))
        scope['query_string'] = b''
        marker = callback_query.set(params)
        try:
            await self.app(scope, receive, send)
        finally:
            callback_query.reset(marker)
