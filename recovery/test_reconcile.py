import copy
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import reconcile as r


def hold_parent(parent, ready, release):
    fd=os.open(parent,os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd,fcntl.LOCK_EX)
    ready.set();release.wait(10);os.close(fd)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700);self.dest=self.root/'recovery'
        self.config={'threads':{'expected_user_id':'123','expected_username':'example',
                               'instagram_login_mode':None,'access_token':'synthetic-secret'}}
        self.manifest={'version':1,'source_runtime':'old-runtime','source_record':'approved-record',
                       'inventory_complete':False,'old_writers_stopped':False,'attempts':[]}
        self.row={'platform':'threads','account_id':'123','id':'456',
                  'timestamp':'2026-11-22T11:50:50+0000',
                  'permalink':'https://www.threads.com/@example/post/abc',
                  'media_type':'TEXT_POST','text_sha256':'a'*64}

    def init(self):
        return r.initialize(self.dest,self.manifest,self.config,fetch=lambda *a:[self.row])

    def snapshot(self):
        return {x.name:x.read_bytes() for x in self.dest.iterdir()}

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
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

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_unknown_attempt_is_quarantined_not_successful(self):
        self.manifest['attempts']=[{'platform':'threads','job_id':'uncertain','post_id':'999',
                                  'content_hash':None,'media_sha256':None}]
        self.init()
        with sqlite3.connect(self.dest/'recovery.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state FROM attempts').fetchone(),('quarantined',))
        self.assertIn('unresolved_attempts',r.inspect(self.dest)['reasons'])

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_known_reference_does_not_invent_missing_hash(self):
        self.manifest['attempts']=[{'platform':'threads','job_id':'old-job','post_id':'456',
                                  'content_hash':None,'media_sha256':None}]
        self.init()
        with sqlite3.connect(self.dest/'recovery.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state,content_hash FROM attempts').fetchone(),('confirmed_post_reference',None))

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_repeat_initialization_never_overwrites(self):
        self.init();before=self.snapshot()
        with self.assertRaises(r.Blocked):self.init()
        self.assertEqual(before,self.snapshot())

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_new_job_cannot_bypass_unresolved_history(self):
        self.init();before=self.snapshot()
        for job in ('same-job','new-job'):
            with self.assertRaises(r.Blocked):r.reserve(self.dest,'threads',job,'b'*64,'c'*64)
        self.assertEqual(before,self.snapshot())

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_api_failure_creates_no_state(self):
        with self.assertRaises(r.p.ApiError):
            r.initialize(self.dest,self.manifest,self.config,fetch=mock.Mock(side_effect=r.p.ApiError(403)))
        self.assertFalse(self.dest.exists())

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
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

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_parent_lock_blocks_separate_process(self):
        ctx=multiprocessing.get_context('fork');ready=ctx.Event();release=ctx.Event()
        process=ctx.Process(target=hold_parent,args=(self.root,ready,release));process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaises(BlockingIOError):self.init()
            self.assertFalse(self.dest.exists())
        finally:release.set();process.join(5)
        self.assertEqual(process.exitcode,0)

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
    def test_original_video_hash_not_inferred_from_graph(self):
        self.row['media_type']='VIDEO';self.init()
        self.assertIn('original_media_identity_unverified',r.inspect(self.dest)['reasons'])

    @unittest.skipIf((Path(tempfile.gettempdir()) / ".git").exists(), "Runtime guard intentionally rejects this environment temporary Git root; no bypass")
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
        if not (Path(tempfile.gettempdir()) / '.git').exists():
            self.skipTest('This host has a valid non-Git temporary root')
        with self.assertRaises(r.p.SafeError):self.init()
        self.assertFalse(self.dest.exists())

    def test_unknown_manifest_fields_reject_secret_storage(self):
        self.manifest['access_token']='do-not-store'
        with self.assertRaises(r.Blocked):r.validate_manifest(self.manifest)

    def test_future_timestamp_rejected(self):
        with self.assertRaises(r.Blocked):r.timestamp('2999-01-01T00:00:00Z')

if __name__=='__main__':unittest.main()
