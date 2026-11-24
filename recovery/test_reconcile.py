import copy
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import reconcile as r


def hold_parent(parent, ready, release):
    fd=os.open(parent,os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd,fcntl.LOCK_EX)
    ready.set();release.wait(10);os.close(fd)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=os.environ.get("MORU_TEST_ROOT"));self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700);self.dest=self.root/'recovery'
        self.config={'threads':{'expected_user_id':'123','expected_username':'example',
                               'instagram_login_mode':None,'access_token':'synthetic-secret'}}
        self.manifest={'version':1,'source_runtime':'old-runtime','source_record':'approved-record',
                       'inventory_complete':False,'old_writers_stopped':False,'attempts':[]}
        self.row={'platform':'threads','account_id':'123','id':'456',
                  'timestamp':'2026-11-22T11:50:50+0000',
                  'permalink':'https://www.threads.com/@example/post/abc',
                  'media_type':'TEXT_POST','text_sha256':'a'*64,'content_hash':r.p.content_hash('old text')}

    def init(self):
        return r.initialize(self.dest,self.manifest,self.config,fetch=lambda *a:[self.row])

    def snapshot(self):
        return {x.name:x.read_bytes() for x in self.dest.iterdir()}

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_official_metadata_without_forged_publisher_receipt(self):
        self.init()
        self.assertFalse((self.dest/'runtime.json').exists())
        self.assertFalse((self.dest/'publisher.sqlite3').exists())
        with sqlite3.connect(self.dest/'recovery.sqlite3') as db:
            self.assertEqual(db.execute('SELECT post_id,permalink FROM posts').fetchone(),('456',self.row['permalink']))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone(),(0,))
        self.assertFalse(r.inspect(self.dest)['publish_ready'])
        self.assertNotIn(b'synthetic-secret',(self.dest/'recovery.sqlite3').read_bytes())
        self.assertEqual((self.dest/'recovery.sqlite3').stat().st_mode & 0o777,0o600)

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_unknown_attempt_is_quarantined_not_successful(self):
        self.manifest['attempts']=[{'platform':'threads','job_id':'uncertain','post_id':'999',
                                  'content_hash':None,'media_sha256':None}]
        self.init()
        with sqlite3.connect(self.dest/'recovery.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state FROM attempts').fetchone(),('quarantined',))
        self.assertIn('unresolved_attempts',r.inspect(self.dest)['reasons'])

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_known_reference_does_not_invent_missing_hash(self):
        self.manifest['attempts']=[{'platform':'threads','job_id':'old-job','post_id':'456',
                                  'content_hash':None,'media_sha256':None}]
        self.init()
        with sqlite3.connect(self.dest/'recovery.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state,content_hash FROM attempts').fetchone(),('confirmed_post_reference',None))

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_repeat_initialization_never_overwrites(self):
        self.init();before=self.snapshot()
        with self.assertRaises(r.Blocked):self.init()
        self.assertEqual(before,self.snapshot())

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_new_job_cannot_bypass_unresolved_history(self):
        self.init();before=self.snapshot()
        for job in ('same-job','new-job'):
            with self.assertRaises(r.Blocked):r.reserve(self.dest,'threads',job,'b'*64,'c'*64)
        self.assertEqual(before,self.snapshot())

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_api_failure_creates_no_state(self):
        with self.assertRaises(r.p.ApiError):
            r.initialize(self.dest,self.manifest,self.config,fetch=mock.Mock(side_effect=r.p.ApiError(403)))
        self.assertFalse(self.dest.exists())

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_wrong_account_creates_no_state(self):
        self.row['account_id']='999'
        with self.assertRaises(r.Blocked):self.init()
        self.assertFalse(self.dest.exists())

    def test_symlink_rejected_without_touching_target(self):
        target=self.root/'target';target.mkdir(mode=0o700);self.dest.symlink_to(target)
        with self.assertRaises(r.p.SafeError):self.init()
        self.assertEqual(list(target.iterdir()),[])

    def test_duplicate_jobs_rejected_before_api(self):
        attempt={'platform':'threads','job_id':'same','post_id':None,'content_hash':None,'media_sha256':None}
        self.manifest['attempts']=[attempt,copy.deepcopy(attempt)]
        fetch=mock.Mock()
        with self.assertRaises(r.Blocked):r.initialize(self.dest,self.manifest,self.config,fetch)
        fetch.assert_not_called()

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_parent_lock_blocks_separate_process(self):
        ctx=multiprocessing.get_context('fork');ready=ctx.Event();release=ctx.Event()
        process=ctx.Process(target=hold_parent,args=(self.root,ready,release));process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaises(BlockingIOError):self.init()
            self.assertFalse(self.dest.exists())
        finally:release.set();process.join(5)
        self.assertEqual(process.exitcode,0)

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_original_video_hash_not_inferred_from_graph(self):
        self.row['media_type']='VIDEO';self.init()
        self.assertIn('original_media_identity_unverified',r.inspect(self.dest)['reasons'])

    @unittest.skipIf((Path(os.environ.get("MORU_TEST_ROOT",tempfile.gettempdir())) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_manifest_mutation_detected(self):
        self.init()
        with sqlite3.connect(self.dest/'recovery.sqlite3') as db:
            changed={**self.manifest,'inventory_complete':True}
            db.execute('UPDATE metadata SET value=? WHERE key=?',(json.dumps(changed),'manifest'))
        with self.assertRaises(r.Blocked):r.inspect(self.dest)

    def test_graph_pagination_uses_only_fixed_edge_and_cursor(self):
        raw={k:self.row[k] for k in ('id','permalink','timestamp','media_type')};raw['text']='hello'
        raw2={**raw,'id':'457'}
        responses=[{'data':[raw],'paging':{'next':'https://evil.invalid/secret','cursors':{'after':'abc'}}}, {'data':[raw2]}]
        with mock.patch.object(r.runtime,'verify_identity'),mock.patch.object(r.p.APIClient,'request',side_effect=responses) as call:
            rows=r.fetch_owned('threads',self.config['threads'])
        self.assertEqual(len(rows),2)
        self.assertTrue(all(c.args[:2]==('GET','/123/threads') for c in call.call_args_list))
        self.assertEqual(call.call_args_list[1].kwargs['params']['after'],'abc')
        self.assertNotIn('hello',json.dumps(rows))

    def test_pagination_without_cursor_fails_closed(self):
        with mock.patch.object(r.runtime,'verify_identity'),mock.patch.object(r.p.APIClient,'request',return_value={'data':[],'paging':{'next':'https://evil.invalid'}}):
            with self.assertRaises(r.Blocked):r.fetch_owned('threads',self.config['threads'])

    def test_actual_runtime_path_guard_remains_enforced(self):
        # Exercise the real Git-boundary refusal independently of valid private fixtures.
        git_root=self.root/'checkout';git_root.mkdir(mode=0o700);(git_root/'.git').mkdir()
        target=git_root/'state'
        with self.assertRaises(r.p.SafeError):r.initialize(target,self.manifest,self.config,fetch=lambda *a:[self.row])
        self.assertFalse(target.exists())

    def test_unknown_manifest_fields_reject_secret_storage(self):
        self.manifest['access_token']='do-not-store'
        with self.assertRaises(r.Blocked):r.validate_manifest(self.manifest)

    def test_future_timestamp_rejected(self):
        with self.assertRaises(r.Blocked):r.timestamp('2999-01-01T00:00:00Z')

    def migrated(self):
        self.manifest['inventory_complete']=True
        self.manifest['old_writers_stopped']=True
        self.init()
        dest=self.root/'native'
        result=r.migrate(self.dest,dest,'recovered-test-v1')
        self.assertEqual(result['native_v1_guard'],'passed')
        return dest

    def test_native_runtime_accepts_migration_and_preserves_evidence(self):
        dest=self.migrated();before=self.snapshot()
        self.assertEqual(r.p.inspect_state(dest,'recovered-test-v1'),1)
        r.p.require_state_binding(dest,'threads','123')
        ledger=r.p.Ledger(dest)
        try:
            row=ledger.get(('threads','123','imported.456'))
            self.assertEqual(row['state'],'published')
            self.assertEqual(row['post_id'],'456')
            self.assertEqual(row['updated_at'],r.timestamp(self.row['timestamp']))
        finally:ledger.close()
        self.assertEqual(self.snapshot(),before)

    def test_pre_recovery_executor_rejects_migrated_marker(self):
        dest=self.migrated()
        # Load the exact parent-commit parser independently; no operational installation used.
        original=subprocess.check_output(['git','show','9e2d27d:plugins/moru-sns/skills/sns-direct-publisher/scripts/publisher.py'],text=True)
        module=types.ModuleType('legacy_fixture')
        module.__file__=str(r.p.__file__)
        with mock.patch.dict(sys.modules,legacy_fixture=module):
            exec(compile(original,'legacy_fixture','exec'),module.__dict__)
            with self.assertRaises(module.SafeError):module.inspect_state(dest)

    def test_native_duplicate_and_same_id_retry_do_not_create_work(self):
        ledger=r.p.Ledger(self.migrated())
        try:
            row,fresh=ledger.claim(('threads','123','imported.456'),r.p.content_hash('old text'))
            self.assertFalse(fresh)
            self.assertEqual(row['state'],'published')
            with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123','new-id'),r.p.content_hash('old text'))
            with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123','imported.456'),'b'*64)
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        finally:ledger.close()

    def test_native_unknown_attempt_blocks_every_new_id(self):
        self.manifest['attempts']=[{'platform':'threads','job_id':'unknown','post_id':None,'content_hash':None,'media_sha256':None}]
        ledger=r.p.Ledger(self.migrated())
        try:
            with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123','new-id'),'c'*64)
            self.assertEqual(ledger.get(('threads','123','unknown'))['state'],'unknown')
        finally:ledger.close()

    def test_native_media_missing_source_hash_blocks(self):
        self.row['media_type']='VIDEO'
        ledger=r.p.Ledger(self.migrated())
        try:
            with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123','new-id'),'c'*64)
        finally:ledger.close()

    def test_native_concurrent_writer_rejected(self):
        dest=self.migrated();ledger=r.p.Ledger(dest)
        try:
            with self.assertRaises(BlockingIOError):r.p.Ledger(dest)
        finally:ledger.close()
        second=r.p.Ledger(dest);second.close()

    def test_native_interval_uses_official_publication_time(self):
        self.row['timestamp']=r.datetime.now(r.timezone.utc).isoformat()
        ledger=r.p.Ledger(self.migrated())
        try:
            with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123','new-id'),'c'*64)
        finally:ledger.close()

    def test_partial_migration_has_no_usable_marker(self):
        self.init();dest=self.root/'partial'
        def fail(ledger):raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            r.guard.initialize_state(dest,'partial',{'threads':{'account_id':'123','username':'example','auth_mode':None}},populate=fail)
        self.assertFalse((dest/'runtime.json').exists())
        with self.assertRaises(Exception):r.p.inspect_state(dest)
        with self.assertRaises(r.p.SafeError):r.migrate(self.dest,dest,'partial')

    def test_native_history_drift_prevents_post_and_claim(self):
        ledger=r.p.Ledger(self.migrated());client=mock.Mock()
        client.request.side_effect=[{'id':'123'},{'data':[{'id':'456'},{'id':'999'}]}]
        try:
            publisher=r.p.Publisher('threads','123',client,ledger,wait=lambda _:None)
            with self.assertRaises(r.p.SafeError):publisher.publish('new','new text')
            self.assertIsNone(ledger.get(('threads','123','new')))
            self.assertTrue(all(c.args[0]=='GET' for c in client.request.call_args_list))
        finally:ledger.close()

    def test_official_link_and_recent_time_block_before_claim(self):
        ledger=r.p.Ledger(self.migrated())
        try:
            for change in ({'permalink':'https://www.threads.com/@example/post/changed'},
                           {'timestamp':r.p.datetime.now().astimezone().isoformat()},
                           {'timestamp':'invalid'}):
                client=mock.Mock()
                client.request.side_effect=[{'id':'123'},{'data':[{**self.row,**change}]}]
                with self.assertRaises(r.p.SafeError):
                    r.p.Publisher('threads','123',client,ledger).publish('new','new text')
                self.assertIsNone(ledger.get(('threads','123','new')))
                self.assertTrue(all(c.args[0]=='GET' for c in client.request.call_args_list))
        finally:ledger.close()

    def test_six_hour_gate_is_platform_and_account_scoped(self):
        ledger=r.p.Ledger(self.migrated())
        try:
            for platform,account,job in [('instagram','999','ig-recent'),('threads','999','other-account')]:
                ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,content_hash,state,updated_at) VALUES(?,?,?,?,'published',?)",
                                  (platform,account,job,job,r.time.time()))
            _,fresh=ledger.claim(('threads','123','new'),'d'*64)
            self.assertTrue(fresh)
        finally:ledger.close()

    def test_native_engine_completes_mock_publication(self):
        ledger=r.p.Ledger(self.migrated());client=mock.Mock()
        def request(method,path,*args,**kwargs):
            if path=='/me':return {'id':'123'}
            if path=='/123/threads' and method=='GET':return {'data':[self.row]}
            if path=='/123/threads':return {'id':'789'}
            if path=='/789':return {'status':'FINISHED'}
            if path=='/123/threads_publish':return {'id':'888'}
            if path=='/888':return {'id':'888','permalink':'https://www.threads.com/@example/post/new'}
            raise AssertionError(path)
        client.request.side_effect=request
        try:
            publisher=r.p.Publisher('threads','123',client,ledger,wait=lambda _:None)
            result=publisher.publish('new','new text')
            self.assertEqual(result['state'],'published')
            self.assertEqual(result['post_id'],'888')
        finally:ledger.close()

    def test_native_uncertain_post_is_not_resubmitted(self):
        ledger=r.p.Ledger(self.migrated());client=mock.Mock()
        client.request.side_effect=[{'id':'123'},{'data':[self.row]},{'id':'123'},r.p.ApiError()]
        try:
            publisher=r.p.Publisher('threads','123',client,ledger,wait=lambda _:None)
            self.assertEqual(publisher.publish('new','new text')['state'],'unknown')
            for key in ('new','another-id'):
                with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123',key),r.p.content_hash('new text'))
            self.assertEqual(sum(c.args[0]=='POST' for c in client.request.call_args_list),1)
        finally:ledger.close()

if __name__=='__main__':unittest.main()
