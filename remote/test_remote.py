"""Isolated tests only: synthetic credentials, temporary ledgers, fake Meta API."""
import asyncio
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import engine as bridge
from engine import Engine, Media, Blocked
import runtime_guard
from server import create_server, create_app, BoundVerifier
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient



def publication_worker(state, bindings, entered, release, output):
    """Separate interpreter, same on-disk fixture; no external API transport."""
    engine = Engine(state_dir=state, runtime_id='test-runtime', bindings=bindings, publishing_enabled=True)
    responses = iter([{'id': '123', 'username': 'test_bot'}, {'id': '100'},
                      {'status': 'FINISHED'}, {'id': '200'},
                      {'id': '200', 'permalink': 'https://www.threads.com/@test_bot/post/fixture'}])
    count = 0
    def request(*args, **kwargs):
        nonlocal count
        count += 1
        return next(responses)
    def identity(*args):
        if entered is not None:
            entered.set()
            if not release.wait(10):
                raise AssertionError('Parent did not release fixture')
        return '123'
    publisher_class = bridge.p.Publisher
    try:
        with mock.patch.object(bridge.runtime, 'verify_identity', side_effect=identity), \
                mock.patch.object(bridge.p.APIClient, 'request', side_effect=request), \
                mock.patch.object(bridge.p, 'Publisher', side_effect=lambda *a, **kw: publisher_class(*a, wait=lambda _: None, **kw)):
            result = engine.call('publish', 'threads', job_id='shared-job', text='synthetic note',
                                 reviewed_hash=bridge.p.content_hash('synthetic note'))
        output.put((result, count))
    except Blocked as error:
        output.put(({'blocked': str(error)}, count))

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.bindings = {'threads': {'account_id': '123', 'username': 'test_bot', 'auth_mode': None},
                         'instagram': {'account_id': '456', 'username': 'test_bot', 'auth_mode': 'unified'}}
        env = {}
        for platform, binding in self.bindings.items():
            env.update({platform.upper() + '_USER_ACCESS_TOKEN': 'synthetic-' + platform + '-token',
                        platform.upper() + '_EXPECTED_USER_ID': binding['account_id'],
                        platform.upper() + '_EXPECTED_USERNAME': binding['username']})
        env['INSTAGRAM_LOGIN_MODE'] = 'unified'
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        runtime_guard.initialize_state(self.state, 'test-runtime', self.bindings)
        self.engine = Engine(state_dir=self.state, runtime_id='test-runtime', bindings=self.bindings)

    def snapshot(self):
        return {f.name: f.read_bytes() for f in self.state.iterdir()}

    def row(self, job_id, text, state='published', age=0):
        ledger = bridge.p.Ledger(str(self.state))
        key = ('threads', '123', job_id)
        ledger.claim(key, bridge.p.content_hash(text))
        ledger.update(key, state, post_id='789' if state == 'published' else None)
        ledger.db.execute('UPDATE jobs SET updated_at=? WHERE job_id=?', (time.time() - age, job_id))
        ledger.close()

    def test_two_processes_share_lock_and_persistent_receipt(self):
        ctx = multiprocessing.get_context('spawn')
        entered, release, output = ctx.Event(), ctx.Event(), ctx.Queue()
        first = ctx.Process(target=publication_worker, args=(self.state, self.bindings, entered, release, output))
        first.start()
        try:
            self.assertTrue(entered.wait(10), 'First process must hold the marker lock')
            contender = ctx.Process(target=publication_worker, args=(self.state, self.bindings, None, None, output))
            contender.start()
            contender.join(10)
            if contender.is_alive():
                contender.terminate()
                contender.join()
                self.fail('Competing process did not fail promptly')
            blocked, calls = output.get(timeout=5)
            self.assertIn('blocked', blocked)
            self.assertEqual(calls, 0)
        finally:
            release.set()
            first.join(10)
            if first.is_alive():
                first.terminate()
                first.join()
        published, calls = output.get(timeout=5)
        self.assertEqual(published['state'], 'published')
        self.assertEqual(calls, 5)
        retry = ctx.Process(target=publication_worker, args=(self.state, self.bindings, None, None, output))
        retry.start()
        retry.join(10)
        if retry.is_alive():
            retry.terminate()
            retry.join()
            self.fail('Retry process did not finish')
        replay, calls = output.get(timeout=5)
        self.assertTrue(replay['network_requests_skipped'])
        self.assertEqual(calls, 0)
        ledger = bridge.p.Ledger(str(self.state))
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        ledger.close()
        output.close()

    def test_enablement_requires_boolean_and_missing_state_never_created(self):
        for value in ('false', 'true', 1, None):
            with self.assertRaises(ValueError):
                Engine(state_dir=self.state, runtime_id='test-runtime', bindings=self.bindings, publishing_enabled=value)
        self.engine.publishing_enabled = True
        self.engine.state_dir = self.root / 'missing'
        with mock.patch.object(bridge.runtime, 'verify_identity') as api:
            with self.assertRaises(Blocked):
                self.engine.call('publish', 'threads', job_id='one', text='note', reviewed_hash=bridge.p.content_hash('note'))
            api.assert_not_called()
        self.assertFalse(self.engine.state_dir.exists())

    def test_media_symlink_permissions_and_missing_profile_fail_before_api(self):
        path = self.root / 'asset.mp4'
        path.write_bytes(b'synthetic')
        path.chmod(0o600)
        link = self.root / 'link.mp4'
        link.symlink_to(path)
        self.engine.media['asset'] = Media(link, 'fixture')
        with mock.patch.object(bridge.p, 'prepare_video') as prepare:
            with self.assertRaises(Blocked):
                self.engine.call('plan', 'instagram', job_id='one', text='caption', media_id='asset')
            prepare.assert_not_called()
            self.engine.media['asset'] = Media(path, 'fixture')
            path.chmod(0o644)
            with self.assertRaises(Blocked):
                self.engine.call('plan', 'instagram', job_id='one', text='caption', media_id='asset')
            prepare.assert_not_called()
            path.chmod(0o600)
            prepare.return_value = mock.Mock(sha256='fixture', preflight='unverified_ffprobe_missing')
            with self.assertRaisesRegex(Blocked, 'media_profile_not_verified'):
                self.engine.call('plan', 'instagram', job_id='one', text='caption', media_id='asset')

    def test_configured_profile_cannot_fall_back_to_remote_text(self):
        import publication_profile
        self.engine.publishing_enabled=True
        before=(self.state/'publisher.sqlite3').read_bytes()
        with mock.patch.object(publication_profile,'configured',return_value={'profile_id':'synthetic'}), \
                mock.patch.object(bridge.runtime,'verify_identity') as identity:
            for operation in ('plan','publish'):
                with self.assertRaisesRegex(Blocked,'profile_evidence_not_supported'):
                    self.engine.call(operation,'threads',job_id='one',text='caption',reviewed_hash='a'*64)
            identity.assert_not_called()
        self.assertEqual(before,(self.state/'publisher.sqlite3').read_bytes())

    def test_plan_no_network_or_state_writes_and_x_rejected(self):
        before = self.snapshot()
        with mock.patch.object(bridge.p.APIClient, 'request', side_effect=AssertionError('No API')):
            result = self.engine.call('plan', 'threads', job_id='one', text='A synthetic development note.')
            self.assertFalse(result['publish_ready'])
            self.assertEqual(result['network_requests'], 0)
            with self.assertRaises(Blocked):
                self.engine.call('status', 'x')
        self.assertEqual(before, self.snapshot())

    def test_write_disabled_missing_ledger_and_changed_account_fail_closed(self):
        with mock.patch.object(bridge.runtime, 'verify_identity', side_effect=AssertionError('No API')):
            with self.assertRaisesRegex(Blocked, 'publishing_disabled'):
                self.engine.call('publish', 'threads', job_id='one', text='note')
            self.engine.state_dir = self.root / 'absent'
            with self.assertRaises(Blocked):
                self.engine.call('check', 'threads')
            self.assertFalse(self.engine.state_dir.exists())
            self.engine.state_dir = self.state
            os.environ['THREADS_EXPECTED_USERNAME'] = 'another_bot'
            with self.assertRaisesRegex(Blocked, 'configured_account_mismatch'):
                self.engine.call('check', 'threads')

    def test_lock_prevents_overlapping_calls(self):
        with (self.state / 'runtime.json').open('r') as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(Blocked):
                self.engine.call('plan', 'threads', job_id='one', text='note')

    def test_cooldown_unresolved_and_duplicate_failed_content(self):
        digest = bridge.p.content_hash('new note')
        self.row('prior', 'old note')
        with self.assertRaisesRegex(Blocked, 'minimum_interval'):
            self.engine._publication_gate('threads', '123', 'new', digest)
        ledger = bridge.p.Ledger(str(self.state))
        ledger.update(('threads', '123', 'prior'), 'unknown')
        ledger.close()
        with self.assertRaisesRegex(Blocked, 'unresolved_prior'):
            self.engine._publication_gate('threads', '123', 'new', digest)
        self.row('failed-job', 'new note', state='failed')
        # A new ID cannot evade failed content, even when unrelated unresolved work is reconciled.
        ledger = bridge.p.Ledger(str(self.state))
        ledger.update(('threads', '123', 'prior'), 'failed')
        ledger.close()
        with self.assertRaisesRegex(Blocked, 'content_already'):
            self.engine._publication_gate('threads', '123', 'new', digest)

    def test_engine_receipt_replay_does_not_publish_again(self):
        self.row('one', 'same note', age=7 * 3600)
        self.engine.publishing_enabled = True
        with mock.patch.object(bridge.runtime, 'verify_identity', return_value='123'), \
                mock.patch.object(bridge.p.APIClient, 'request', side_effect=AssertionError('No publish')):
            result = self.engine.call('publish', 'threads', job_id='one', text='same note',
                                      reviewed_hash=bridge.p.content_hash('same note'))
        self.assertEqual(result['state'], 'published')
        self.assertTrue(result['network_requests_skipped'])

    def test_real_engine_uses_fake_meta_and_records_once(self):
        self.engine.publishing_enabled = True
        responses = iter([{'id': '123', 'username': 'test_bot'}, {'id': '100'},
                          {'status': 'FINISHED'}, {'id': '200'},
                          {'id': '200', 'permalink': 'https://www.threads.com/@test_bot/post/fixture'}])
        publisher_class = bridge.p.Publisher
        with mock.patch.object(bridge.runtime, 'verify_identity', return_value='123'), \
                mock.patch.object(bridge.p, 'Publisher', side_effect=lambda *a, **kw: publisher_class(*a, wait=lambda _: None, **kw)), \
                mock.patch.object(bridge.p.APIClient, 'request', side_effect=lambda *a, **kw: next(responses)):
            result = self.engine.call('publish', 'threads', job_id='one', text='same note',
                                      reviewed_hash=bridge.p.content_hash('same note'))
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['post_id'], '200')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_hash_and_unregistered_media_block_before_api(self):
        self.engine.publishing_enabled = True
        with mock.patch.object(bridge.runtime, 'verify_identity', side_effect=AssertionError('No API')):
            with self.assertRaisesRegex(Blocked, 'reviewed_hash_mismatch'):
                self.engine.call('publish', 'threads', job_id='one', text='note', reviewed_hash='bad')
            for media_id in (None, '/private/movie.mp4', 'https://invalid.test/movie.mp4'):
                with self.assertRaises(Blocked):
                    self.engine.call('plan', 'instagram', job_id='one', text='caption', media_id=media_id)

    def test_unknown_job_is_returned_without_resubmission(self):
        self.row('one', 'same note', state='unknown')
        self.engine.publishing_enabled = True
        with mock.patch.object(bridge.runtime, 'verify_identity', return_value='123'), \
                mock.patch.object(bridge.p.APIClient, 'request', side_effect=AssertionError('No publish')):
            result = self.engine.call('publish', 'threads', job_id='one', text='same note',
                                      reviewed_hash=bridge.p.content_hash('same note'))
        self.assertEqual(result['state'], 'unknown')
        self.assertTrue(result['network_requests_skipped'])

    def test_instagram_engine_uses_resumable_and_keeps_processing_receipt(self):
        path = self.root / 'video.mp4'
        path.write_bytes(b'synthetic media')
        path.chmod(0o600)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.engine.media['asset-1'] = Media(path, digest)
        video = mock.Mock(preflight='verified_local_profile', sha256=digest, ai_generated=False, url=None)
        self.engine.publishing_enabled = True
        responses = iter([{'data': [{'id': '456', 'username': 'test_bot'}]},
                          {'id': '100', 'uri': bridge.p.INSTAGRAM_UPLOAD_BASE + '/100'}])
        with mock.patch.object(bridge.p, 'prepare_video', return_value=video), \
                mock.patch.object(bridge.runtime, 'verify_identity', return_value='456'), \
                mock.patch.object(bridge.p.APIClient, 'request', side_effect=lambda *a, **kw: next(responses)) as api, \
                mock.patch.object(bridge.p.APIClient, 'upload_instagram_video', return_value={'success': True}) as upload:
            result = self.engine.call('publish', 'instagram', job_id='one', text='caption', media_id='asset-1',
                                      reviewed_hash=bridge.p.content_hash('caption', video))
        self.assertEqual(result['state'], 'media_processing')
        self.assertEqual(api.call_args.args[2]['upload_type'], 'resumable')
        upload.assert_called_once()
        ledger = bridge.p.Ledger(str(self.state))
        self.assertEqual(ledger.get(('instagram', '456', 'one'))['upload_mode'], 'resumable')
        ledger.close()

    def test_registered_instagram_uses_engine_video_validation(self):
        path = self.root / 'video.mp4'
        path.write_bytes(b'synthetic media')
        path.chmod(0o600)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.engine.media['asset-1'] = Media(path, digest)
        video = mock.Mock(preflight='verified_local_profile', sha256=digest, ai_generated=False)
        with mock.patch.object(bridge.p, 'prepare_video', return_value=video) as prepare:
            result = self.engine.call('plan', 'instagram', job_id='one', text='caption', media_id='asset-1')
            self.assertEqual(result['media_id'], 'asset-1')
            self.assertTrue(prepare.call_args.kwargs['allow_missing_url'])
            video.sha256 = 'changed'
            with self.assertRaisesRegex(Blocked, 'media_hash_mismatch'):
                self.engine.call('plan', 'instagram', job_id='one', text='caption', media_id='asset-1')


class FakeVerifier:
    async def verify_token(self, token):
        if token not in ('synthetic-read', 'synthetic-write'):
            return None
        return AccessToken(token=token, client_id='test-client', subject='test-owner',
                           scopes=['moru:read'] + (['moru:publish'] if token.endswith('write') else []),
                           resource='https://moru.test/mcp', expires_at=int(time.time()) + 3600)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.engine = mock.Mock()
        self.engine.call.return_value = {'configuration': 'synthetic'}
        self.server = create_server(self.engine, verifier=FakeVerifier(), issuer_url='https://issuer.test',
                                   resource_url='https://moru.test/mcp', allowed_clients={'test-client'},
                                   allowed_subjects={'test-owner'}, allowed_origins=['https://chatgpt.com'])
        self.client = self.enterContext(TestClient(create_app(self.server), base_url='https://moru.test'))

    def rpc(self, method, params=None, token='synthetic-read', **headers):
        headers.update({'accept': 'application/json, text/event-stream'})
        if token:
            headers['authorization'] = 'Bearer ' + token
        return self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method,
                                             'params': params or {}}, headers=headers)

    def test_metadata_advertises_scopes_without_requiring_publish_for_reads(self):
        response = self.client.get('/.well-known/oauth-protected-resource/mcp')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['scopes_supported'], ['moru:read', 'moru:publish'])
        result = self.rpc('tools/call', {'name': 'moru_status', 'arguments': {'platform': 'threads'}})
        self.assertEqual(result.status_code, 200)
        self.engine.call.assert_called_once_with('status', 'threads')

    def test_auth_challenge_and_initialization(self):
        self.assertEqual(self.rpc('initialize', token=None).status_code, 401)
        self.assertEqual(self.rpc('initialize', token='invalid').status_code, 401)
        result = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                         'clientInfo': {'name': 'offline-test', 'version': '1'}})
        self.assertEqual(result.status_code, 200)
        self.assertIn('capabilities', result.json()['result'])
        self.engine.call.assert_not_called()

    def test_tools_no_paths_or_x_and_write_scope_required(self):
        result = self.rpc('tools/list').json()['result']['tools']
        self.assertEqual(len(result), 5)
        for tool in result:
            schema = tool['inputSchema']['properties']
            self.assertEqual(schema['platform']['enum'], ['threads', 'instagram'])
            self.assertTrue(set(schema).isdisjoint({'token', 'state_dir', 'config', 'video_file', 'video_url'}))
        args = {'platform': 'threads', 'job_id': 'one', 'text': 'note', 'reviewed_hash': 'hash'}
        result = self.rpc('tools/call', {'name': 'moru_publish', 'arguments': args}).json()
        self.assertIn('insufficient_scope', json.dumps(result))
        self.engine.call.assert_not_called()
        self.rpc('tools/call', {'name': 'moru_publish', 'arguments': args}, token='synthetic-write')
        self.engine.call.assert_called_once()

    def test_bad_origin_host_and_platform_never_reach_engine(self):
        self.assertEqual(self.rpc('tools/list', origin='https://evil.test').status_code, 403)
        self.assertEqual(self.rpc('tools/list', host='evil.test').status_code, 421)
        response = self.rpc('tools/call', {'name': 'moru_status', 'arguments': {'platform': 'x'}})
        self.assertTrue(response.json()['result']['isError'])
        self.engine.call.assert_not_called()

    def test_streamable_http_notification_version_and_discovery(self):
        challenge = self.rpc('tools/list', token=None)
        self.assertIn('resource_metadata=', challenge.headers['www-authenticate'])
        metadata = self.client.get('/.well-known/oauth-protected-resource/mcp').json()
        self.assertEqual(metadata['resource'], 'https://moru.test/mcp')
        self.assertEqual(metadata['authorization_servers'], ['https://issuer.test/'])
        headers = {'authorization': 'Bearer synthetic-read',
                   'accept': 'application/json, text/event-stream', 'mcp-protocol-version': '2025-06-18'}
        response = self.client.post('/mcp', headers=headers,
                                    json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'')
        response = self.rpc('tools/list', **{'mcp-protocol-version': 'unsupported'})
        self.assertEqual(response.status_code, 400)
        self.engine.call.assert_not_called()

    def test_official_sdk_client_initializes_lists_and_calls(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async def exercise():
            server = create_server(self.engine, verifier=FakeVerifier(), issuer_url='https://issuer.test',
                                   resource_url='https://moru.test/mcp', allowed_clients={'test-client'},
                                   allowed_subjects={'test-owner'}, allowed_origins=['https://chatgpt.com'])
            app = create_app(server)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        headers={'authorization': 'Bearer synthetic-read'}) as client:
                    async with streamable_http_client('https://moru.test/mcp', http_client=client) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            initialized = await session.initialize()
                            self.assertEqual(initialized.serverInfo.name, 'Moru SNS')
                            listed = await session.list_tools()
                            self.assertEqual(len(listed.tools), 5)
                            result = await session.call_tool('moru_status', {'platform': 'threads'})
                            self.assertFalse(result.isError)
                            self.assertEqual(json.loads(result.content[0].text)['configuration'], 'synthetic')
        # A protocol regression must fail promptly rather than leave an open stream.
        asyncio.run(asyncio.wait_for(exercise(), timeout=10))

    def test_write_only_scope_cannot_bypass_read_requirement(self):
        token = asyncio.run(FakeVerifier().verify_token('synthetic-write'))
        with mock.patch.object(FakeVerifier, 'verify_token', new=mock.AsyncMock(
                return_value=token.model_copy(update={'scopes': ['moru:publish']}))):
            self.assertEqual(self.rpc('tools/list', token='synthetic-write').status_code, 403)
        self.engine.call.assert_not_called()

    def test_verifier_binds_resource_owner_client_and_expiry(self):
        good = asyncio.run(FakeVerifier().verify_token('synthetic-read'))
        delegate = mock.Mock()
        bound = BoundVerifier(delegate, good.resource, {'test-client'}, {'test-owner'})
        for field, value in [('resource', 'https://elsewhere.test/mcp'), ('subject', 'other'),
                             ('client_id', 'other'), ('expires_at', None), ('expires_at', 1)]:
            delegate.verify_token = mock.AsyncMock(return_value=good.model_copy(update={field: value}))
            self.assertIsNone(asyncio.run(bound.verify_token('synthetic-read')))


if __name__ == '__main__':
    unittest.main()
