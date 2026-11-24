import copy
from dataclasses import replace
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import reconcile as r
import candidate_review as review
import delivery_binding as delivery


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        # Historical review protocol with fake media, not a supported new route.
        gate=mock.patch('video_preflight.require_new');gate.start();self.addCleanup(gate.stop)
        self.temp=tempfile.TemporaryDirectory(dir=os.environ.get('MORU_TEST_ROOT'))
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.media=self.write('new.mp4',b'synthetic-new-source-for-offline-tests')
        self.text=self.write('caption.txt',b'New desk development')
        self.artifacts=self.root/'artifacts';self.artifacts.mkdir(mode=0o700)
        self.config={k:{'expected_user_id':a,'expected_username':'example','instagram_login_mode':mode,'access_token':'synthetic'}
                     for k,a,mode in [('threads','123',None),('instagram','999','instagram')]}
        self.raw={k:{'id':post,'timestamp':'2026-11-22T11:50:50+0000','media_type':'VIDEO',
                     'permalink':link,'text':'Old footage','caption':'Old footage'}
                  for k,post,link in [('threads','456','https://www.threads.com/@example/post/abc'),
                                     ('instagram','777','https://www.instagram.com/reel/abc/')]}
        self.rows=[review.receipt(k,c['expected_user_id'],self.raw[k]) for k,c in self.config.items()]
        self.rows.sort(key=lambda x:(x['platform'],x['account_id'],x['post_id']))
        manifest={'version':1,'source_runtime':'old','source_record':'record','inventory_complete':True,'old_writers_stopped':True,'attempts':[]}
        evidence=self.root/'evidence';self.dest=self.root/'native'
        r.initialize(evidence,manifest,self.config,fetch=lambda k,c:[{**self.raw[k],'platform':k,'account_id':c['expected_user_id'],'text_sha256':review.digest('Old footage'),'content_hash':None}])
        r.migrate(evidence,self.dest,'candidate-test')
        self.video=r.p.Video(self.media,review.file_digest(self.media),self.media.stat().st_size,r.p.file_stamp(self.media),31,'verified_local_profile','https://example.com/approved.mp4')
        # This suite isolates candidate consumption; delivery transport is covered separately.
        patcher=mock.patch.object(delivery,'verify');patcher.start();self.addCleanup(patcher.stop);self.delivery_patcher=patcher
        handoff=mock.patch.object(delivery,'check_handoff');handoff.start();self.addCleanup(handoff.stop);self.handoff_patcher=handoff
        self.doc={'version':2,'runtime_id':'candidate-test','review_id':'review-new','approval_record':'user-current-approval','reviewer':'operator',
                  'candidate':{'platform':'threads','account_id':'123','job_id':'one-new-job','media_sha256':self.video.sha256,
                               'content_hash':r.p.content_hash(self.text.read_text(),self.video),'text_sha256':review.file_digest(self.text),'source_record':'new-capture','ai_generated':False},
                  'history':copy.deepcopy(self.rows),'comparisons':[],
                  'delivery':{'account_id':'999','post_id':'777','job_id':'prior-pair','source_sha256':self.video.sha256,
                              'url_sha256':review.digest(self.video.url),'remote_sha256':self.video.sha256,'bytes':self.video.size,
                              'inspection_sha256':'a'*64,'expires_at':r.time.time()+1200}}
        for row in self.rows:
            artifact=self.artifact(('public-rendition-'+row['platform']).encode())
            report=self.artifact(('Full mocked comparison report for '+row['platform']).encode())
            self.doc['comparisons'].append({**{k:row[k] for k in ('platform','account_id','post_id')},
                'source_kind':'official_public_reencoding','artifact_sha256':artifact,'receipt_source_record':'official-download-record',
                'comparison_record':'full-review-record','report_sha256':report,'complete':True,'verdict':'distinct','paired_job_id':None})

    def write(self,name,data):
        path=self.root/name;path.write_bytes(data);path.chmod(0o600);return path

    def artifact(self,data):
        digest=hashlib.sha256(data).hexdigest();path=self.artifacts/digest;path.write_bytes(data);path.chmod(0o600);return digest

    def register(self,doc=None,rows=None):
        return review.register(self.dest,doc or self.doc,self.media,self.text,self.artifacts,collector=lambda _:self.rows if rows is None else rows)

    def claim(self,ledger,doc=None,rows=None):
        c=(doc or self.doc)['candidate'];key=c['platform'],c['account_id'],c['job_id']
        with mock.patch.object(review,'collect',return_value=self.rows if rows is None else rows):
            ledger.prepare_review(key,mock.Mock(),video=self.video)
        return ledger.claim(key,c['content_hash'],auth_mode='instagram' if c['platform']=='instagram' else None,media_sha256=c['media_sha256'])

    def test_exact_candidate_claim_consumes_review_without_inventing_old_hashes(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            _,fresh=self.claim(ledger);self.assertTrue(fresh)
            self.assertEqual(ledger.db.execute('SELECT state FROM recovery_reviews').fetchone()[0],'consumed')
            self.assertTrue(all(tuple(row)==(None,None) for row in ledger.db.execute('SELECT content_hash,media_sha256 FROM recovery_posts')))
            stored=json.loads(ledger.db.execute('SELECT document FROM recovery_reviews').fetchone()[0])
            self.assertTrue(all(i['source_kind']=='official_public_reencoding' for i in stored['comparisons']))
            with self.assertRaises(r.p.SafeError):self.claim(ledger)
        finally:ledger.close()

    def test_changed_or_incomplete_review_rejected(self):
        cases=[]
        doc=copy.deepcopy(self.doc);doc['comparisons'].pop();cases.append(doc)
        for field,value in [('verdict','duplicate'),('complete',False),('source_kind','unverified'),('artifact_sha256','0'*64)]:
            doc=copy.deepcopy(self.doc);doc['comparisons'][0][field]=value;cases.append(doc)
        doc=copy.deepcopy(self.doc);doc['history'][0]['permalink']+='changed';cases.append(doc)
        doc=copy.deepcopy(self.doc);doc['candidate']['account_id']='000';cases.append(doc)
        doc=copy.deepcopy(self.doc);doc['candidate']['media_sha256']='0'*64;cases.append(doc)
        doc=copy.deepcopy(self.doc);doc['candidate']['content_hash']='0'*64;cases.append(doc)
        for doc in cases:
            with self.subTest(doc=doc),self.assertRaises((r.p.SafeError,OSError)):
                self.register(doc)

    def test_same_source_artifact_cannot_be_called_distinct(self):
        self.doc['comparisons'][0]['artifact_sha256']=self.artifact(self.media.read_bytes())
        with self.assertRaises(r.p.SafeError):self.register()

    def test_new_plan_cannot_reuse_media_review(self):
        self.register()
        doc=copy.deepcopy(self.doc);doc['review_id']='new-review';doc['candidate']['job_id']='new-plan'
        with self.assertRaises(sqlite3.IntegrityError):self.register(doc)
        ledger=r.p.Ledger(self.dest)
        try:
            with self.assertRaises(r.p.SafeError):self.claim(ledger,doc)
        finally:ledger.close()

    def test_changed_media_or_content_cannot_claim(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            for field in ('media_sha256','content_hash'):
                doc=copy.deepcopy(self.doc);doc['candidate'][field]='0'*64
                with self.assertRaises(r.p.SafeError):self.claim(ledger,doc)
            self.assertEqual(ledger.db.execute('SELECT state FROM recovery_reviews').fetchone()[0],'approved')
        finally:ledger.close()

    def test_fresh_other_platform_history_change_blocks(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            for field,value in [('text_sha256','0'*64),('published_at',self.rows[0]['published_at']+1),('permalink','https://www.instagram.com/reel/changed/')]:
                rows=copy.deepcopy(self.rows);rows[0][field]=value
                with self.assertRaises(r.p.SafeError):self.claim(ledger,rows=rows)
            rows=copy.deepcopy(self.rows);rows.pop()
            with self.assertRaises(r.p.SafeError):self.claim(ledger,rows=rows)
        finally:ledger.close()

    def test_unknown_attempt_blocks_registration_and_claim(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state) VALUES('instagram','999','uncertain','unknown')")
            with self.assertRaises(r.p.SafeError):self.claim(ledger)
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.register()

    def test_writer_attestation_still_required(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            value=json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0]);value['old_writers_stopped']=False
            ledger.db.execute("UPDATE recovery_metadata SET value=? WHERE key='provenance'",(review.canonical(value),))
            with self.assertRaises(r.p.SafeError):self.claim(ledger)
        finally:ledger.close()

    def test_direct_claim_without_fresh_collection_is_blocked(self):
        self.register();ledger=r.p.Ledger(self.dest);c=self.doc['candidate']
        try:
            with self.assertRaises(r.p.SafeError):ledger.claim(('threads','123',c['job_id']),c['content_hash'],media_sha256=c['media_sha256'])
        finally:ledger.close()

    def test_failed_attempt_cannot_reuse_consumed_review(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            self.claim(ledger);ledger.update(('threads','123','one-new-job'),'failed')
            with self.assertRaises(r.p.SafeError):self.claim(ledger)
        finally:ledger.close()

    def test_processing_resume_reuses_job_not_authorization(self):
        self.register();ledger=r.p.Ledger(self.dest)
        try:
            self.claim(ledger);ledger.update(('threads','123','one-new-job'),'media_processing')
            row,fresh=self.claim(ledger);self.assertFalse(fresh);self.assertEqual(row['state'],'media_processing')
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM recovery_media').fetchone()[0],1)
        finally:ledger.close()

    def test_official_collector_paginates_without_following_urls(self):
        client=mock.Mock();next_raw={**self.raw['threads'],'id':'457'}
        client.request.side_effect=[{'id':'123','username':'example'}, {'data':[self.raw['threads']],'paging':{'next':'https://never-follow.invalid','cursors':{'after':'cursor'}}},{'data':[next_raw]}]
        rows=review.owned('threads',{'account_id':'123','username':'example'},client)
        self.assertEqual(len(rows),2)
        self.assertEqual(client.request.call_args.args,('GET','/123/threads'))
        self.assertEqual(client.request.call_args.kwargs['params']['after'],'cursor')
        self.assertTrue(all(c.args[0]=='GET' for c in client.request.call_args_list))

    def test_official_instagram_collector_respects_login_identity_modes(self):
        for mode,response,edge in [('instagram',{'user_id':'999','username':'example'},'/me'),
                                   ('unified',{'data':[{'id':'999','username':'example'}]},'/me/assigned_instagram_accounts')]:
            with self.subTest(mode=mode):
                client=mock.Mock();client.request.side_effect=[response,{'data':[self.raw['instagram']]}]
                rows=review.owned('instagram',{'account_id':'999','username':'example','auth_mode':mode},client)
                self.assertEqual(len(rows),1)
                self.assertEqual(client.request.call_args_list[0].args,('GET',edge))
                self.assertEqual(client.request.call_args_list[1].args,('GET','/999/media'))
        client=mock.Mock();client.request.return_value={'id':'999','username':'example'}
        with self.assertRaises(r.p.SafeError):review.owned('instagram',{'account_id':'999','username':'example','auth_mode':'instagram'},client)

    def test_mock_native_video_submission_and_uncertain_retry(self):
        self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
        client.request.side_effect=[{'id':'123'},r.p.ApiError()]
        try:
            publisher=r.p.Publisher('threads','123',client,ledger)
            with mock.patch.object(review,'collect',return_value=self.rows):
                result=publisher.publish('one-new-job',self.text.read_text(),self.video)
                self.assertEqual(result['state'],'unknown')
                with self.assertRaises(r.p.SafeError):publisher.publish('one-new-job',self.text.read_text(),self.video)
            self.assertEqual(sum(c.args[0]=='POST' for c in client.request.call_args_list),1)
        finally:ledger.close()

    def test_mock_native_video_completes_after_processing_resume(self):
        self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
        def request(method,path,*args,**kwargs):
            if path=='/me':return {'id':'123'}
            if path=='/123/threads':return {'id':'888'}
            if path=='/888':return {'status':'FINISHED'}
            if path=='/123/threads_publish':return {'id':'889'}
            if path=='/889':return {'id':'889','permalink':'https://www.threads.com/@example/post/new'}
            raise AssertionError(path)
        client.request.side_effect=request
        try:
            publisher=r.p.Publisher('threads','123',client,ledger)
            with mock.patch.object(review,'collect',return_value=self.rows):
                first=publisher.publish('one-new-job',self.text.read_text(),self.video)
                self.assertEqual(first['state'],'media_processing')
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='one-new-job'")
                result=publisher.publish('one-new-job',self.text.read_text(),self.video)
                self.assertEqual(result['state'],'published')
                self.assertEqual(result['post_id'],'889')
            self.assertEqual(sum(c.args[0]=='POST' for c in client.request.call_args_list),2)
            self.assertEqual(ledger.db.execute('SELECT state FROM recovery_reviews').fetchone()[0],'consumed')
        finally:ledger.close()

    def test_mock_instagram_reel_completes_with_exact_review(self):
        self.doc['candidate'].update(platform='instagram',account_id='999')
        self.doc['delivery']=None
        self.video=replace(self.video,url=None)
        self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
        def request(method,path,*args,**kwargs):
            if path=='/me/assigned_instagram_accounts':return {'data':[{'id':'999','username':'example'}]}
            if path=='/999/media':return {'id':'888','uri':r.p.INSTAGRAM_UPLOAD_BASE+'/888'}
            if path=='/888':return {'status_code':'FINISHED'}
            if path=='/999/media_publish':return {'id':'889'}
            if path=='/889':return {'id':'889','permalink':'https://www.instagram.com/reel/new/'}
            raise AssertionError(path)
        client.request.side_effect=request
        client.upload_instagram_video.return_value={'success':True}
        try:
            publisher=r.p.Publisher('instagram','999',client,ledger,instagram_login_mode='unified',instagram_upload_mode='resumable')
            with mock.patch.object(review,'collect',return_value=self.rows):
                first=publisher.publish('one-new-job',self.text.read_text(),self.video)
                self.assertEqual(first['state'],'media_processing')
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='one-new-job'")
                result=publisher.publish('one-new-job',self.text.read_text(),self.video)
                self.assertEqual(result['state'],'published')
                self.assertEqual(result['post_id'],'889')
            creation=next(c for c in client.request.call_args_list if c.args[1]=='/999/media')
            self.assertEqual(creation.args[2]['media_type'],'REELS')
        finally:ledger.close()

    def test_uncertain_public_publish_response_never_reposts(self):
        self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
        def request(method,path,*args,**kwargs):
            if path=='/me':return {'id':'123'}
            if path=='/123/threads':return {'id':'888'}
            if path=='/888':return {'status':'FINISHED'}
            if path=='/123/threads_publish':raise r.p.ApiError()
            raise AssertionError(path)
        client.request.side_effect=request
        try:
            publisher=r.p.Publisher('threads','123',client,ledger)
            with mock.patch.object(review,'collect',return_value=self.rows):
                publisher.publish('one-new-job',self.text.read_text(),self.video)
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='one-new-job'")
                self.assertEqual(publisher.publish('one-new-job',self.text.read_text(),self.video)['state'],'unknown')
                with self.assertRaises(r.p.SafeError):publisher.publish('one-new-job',self.text.read_text(),self.video)
            self.assertEqual(sum(c.args[1]=='/123/threads_publish' for c in client.request.call_args_list),1)
        finally:ledger.close()

    def test_recent_other_platform_does_not_reset_target_interval(self):
        recent=copy.deepcopy(self.rows)
        recent[0]['published_at']=r.time.time()-10
        self.doc['history']=copy.deepcopy(recent)
        self.register(rows=recent)
        ledger=r.p.Ledger(self.dest)
        try:self.assertTrue(self.claim(ledger,rows=recent)[1])
        finally:ledger.close()

    def test_recent_target_platform_blocks_reviewed_candidate(self):
        recent=copy.deepcopy(self.rows)
        recent[1]['published_at']=r.time.time()-10
        self.doc['history']=copy.deepcopy(recent)
        self.register(rows=recent)
        ledger=r.p.Ledger(self.dest)
        try:
            with self.assertRaisesRegex(r.p.SafeError,'six-hour'):self.claim(ledger,rows=recent)
        finally:ledger.close()

    def test_only_explicit_cross_platform_paired_receipt_allows_same_source(self):
        ledger=r.p.Ledger(self.dest)
        try:
            ledger.db.execute("INSERT INTO recovery_media VALUES('instagram',?,'prior-pair')",(self.video.sha256,))
            ledger.db.execute("UPDATE jobs SET job_id='prior-pair' WHERE platform='instagram'")
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.register()
        item=next(i for i in self.doc['comparisons'] if i['platform']=='instagram')
        item.update(verdict='authorized_cross_platform',paired_job_id='wrong')
        with self.assertRaises(r.p.SafeError):self.register()
        item['paired_job_id']='prior-pair';self.register()
        ledger=r.p.Ledger(self.dest)
        try:self.assertTrue(self.claim(ledger)[1])
        finally:ledger.close()

    def test_same_platform_pair_rejected(self):
        item=next(i for i in self.doc['comparisons'] if i['platform']=='threads')
        item.update(verdict='authorized_cross_platform',paired_job_id='imported.456')
        with self.assertRaises(r.p.SafeError):self.register()

    def test_review_registration_cannot_race_active_writer(self):
        ledger=r.p.Ledger(self.dest)
        try:
            with self.assertRaises(BlockingIOError):self.register()
        finally:ledger.close()

if __name__=='__main__':unittest.main()
