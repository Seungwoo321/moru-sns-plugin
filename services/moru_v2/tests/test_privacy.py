import asyncio
import logging
import httpx
import pytest
from services.moru_v2.domain import Blocked
from services.moru_v2.meta import OfficialMetaProvider
from services.moru_v2.privacy import CallbackPrivacyMiddleware, callback_query, sensitive_http


@pytest.mark.parametrize('failure', [False, True])
def test_meta_canaries_never_reach_http_logs(caplog, failure):
    caplog.set_level(logging.DEBUG)
    canary = 'CANARY-OAUTH-DO-NOT-LOG'
    def transport(request):
        try:
            raise RuntimeError(canary)
        except RuntimeError:
            logging.getLogger('httpcore.connection').warning('request %s', request.url, exc_info=True)
        if failure:
            raise RuntimeError(canary)
        if request.url.path == '/oauth/access_token':
            return httpx.Response(200, json={'access_token': canary, 'user_id': '1'})
        if request.url.path == '/access_token':
            return httpx.Response(200, json={'access_token': canary, 'token_type': 'bearer', 'expires_in': 100})
        return httpx.Response(200, json={'id': '1'})
    provider = OfficialMetaProvider('threads', client_id='123', client_secret=canary,
        callback_uri='https://moru.test/connections/threads/callback', transport=httpx.MockTransport(transport))
    if failure:
        with pytest.raises(Blocked, match='^provider_exchange_failed$'):
            asyncio.run(provider.exchange(canary))
    else:
        asyncio.run(provider.exchange(canary))
    assert caplog.records and canary not in caplog.text
    assert all(r.exc_info is None for r in caplog.records if r.name.startswith('httpcore'))
    logging.getLogger('httpx').info('unrelated-http-request-visible')
    assert 'unrelated-http-request-visible' in caplog.text


def test_redaction_is_task_local_and_preserves_application_logs(caplog):
    caplog.set_level(logging.INFO)
    async def run():
        entered, other_done = asyncio.Event(), asyncio.Event()
        async def secret():
            with sensitive_http():
                entered.set()
                await other_done.wait()
                logging.getLogger('httpx').info('SECRET-CANARY')
                logging.getLogger('moru.audit').info('safe application event')
        async def other():
            await entered.wait()
            logging.getLogger('httpx').info('other request visible')
            other_done.set()
        await asyncio.gather(secret(), other())
    asyncio.run(run())
    assert 'SECRET-CANARY' not in caplog.text
    assert 'other request visible' in caplog.text and 'safe application event' in caplog.text


@pytest.mark.parametrize('fails', [False, True])
def test_callback_scope_is_scrubbed_before_application_and_access_logs(caplog, fails):
    caplog.set_level(logging.INFO)
    scope = {'type': 'http', 'path': '/connections/threads/callback',
             'query_string': b'code=INBOUND-CANARY&state=STATE-CANARY&error_description=ERROR-CANARY'}
    async def downstream(scope, receive, send):
        assert callback_query.get()['code'] == 'INBOUND-CANARY'
        logging.getLogger('uvicorn.access').info('request %s?%s', scope['path'], scope['query_string'])
        if fails:
            raise RuntimeError('sanitized failure')
    app = CallbackPrivacyMiddleware(downstream)
    async def run():
        try:
            await app(scope, None, None)
        finally:
            assert callback_query.get() is None
    if fails:
        with pytest.raises(RuntimeError, match='sanitized'):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert scope['query_string'] == b'' and 'CANARY' not in caplog.text
