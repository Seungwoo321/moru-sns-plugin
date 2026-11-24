import copy
from dataclasses import replace
import hashlib
import io
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import reconcile as r
import candidate_pair as pair
import candidate_review as review
import delivery_binding as delivery


class CandidatePairTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir=os.environ.get('MORU_TEST_ROOT'));self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.root.chmod(0o700)
        self.artifacts=self.root/'artifacts';self.artifacts.mkdir(mode=0o700)
        self.media=self.write('source.mp4',b'synthetic-approved-slack-source')
        self.texts={k:self.write(k+'.txt',('Approved '+k+' caption').encode()) for k in ('threads','instagram')}
        self.source_sha=review.file_digest(self.media)
        self.config={k:{'expected_user_id':a,'expected_username':'synthetic','instagram_login_mode':mode,'access_token':'synthetic-not-used'} for k,a,mode in [('threads','123',None),('instagram','999','unified')]}
        self.rows=[];raw=[]
        for k,a,post,link in [('threads','123','101','https://www.threads.com/@synthetic/post/one'),('threads','123','102','https://www.threads.com/@synthetic/post/two'),('instagram','999','201','https://www.instagram.com/reel/old/')]:
            item={'id':post,'timestamp':'2026-11-22T00:00:00Z','media_type':'VIDEO','text':'Old material','caption':'Old material','permalink':link}
            self.rows.append(review.receipt(k,a,item))
            raw.append({**item,'platform':k,'account_id':a,'text_sha256':review.digest('Old material'),'content_hash':None})
        self.rows.sort(key=lambda i:(i['platform'],i['account_id'],i['post_id']))
        manifest={'version':1,'source_runtime':'legacy','source_record':'legacy-record','inventory_complete':False,'old_writers_stopped':True,
                  'attempts':[{'platform':'threads','job_id':'legacy-unknown','post_id':None,'content_hash':'b'*64,'media_sha256':'c'*64}]}
        r.initialize(self.root/'evidence',manifest,self.config,fetch=lambda k,c:[i for i in raw if i['platform']==k])
        self.dest=self.root/'native';r.migrate(self.root/'evidence',self.dest,'pair-test')
        self.manifest={'version':1,'runtime_id':'pair-test','pair_id':'one-approved-pair','approval_record':'explicit-current-user-approval',
                       'source':{'slack_channel':'D0CDWG6PE4U','slack_message_ts':'1234567890.123456','slack_file_id':'FSYNTHETIC','sha256':self.source_sha},
                       'candidates':[{'platform':k,'account_id':a,'job_id':'pair-'+k,'ordinal':ordinal,'text_sha256':review.file_digest(self.texts[k])} for k,a,ordinal in [('threads','123',3),('instagram','999',2)]],
                       'history':copy.deepcopy(self.rows),'candidate_never_attempted':True,'single_writer_confirmed':True,
                       'evidence_sha256':self.artifact(b'Synthetic comprehensive work-log review: no attempts for this candidate, single writer'),
                       'legacy_unresolved':[{'platform':'threads','account_id':'123','job_id':'legacy-unknown','exclusion_evidence_sha256':self.artifact(b'Synthetic evidence that legacy-unknown concerns different material')} ]}
        self.delivery_patches=[]
        for name in ('verify','check_handoff'):
            patcher=mock.patch.object(delivery,name);patcher.start();self.addCleanup(patcher.stop);self.delivery_patches.append(patcher)

    def write(self,name,data):
        path=self.root/name;path.write_bytes(data);path.chmod(0o600);return path

    def artifact(self,data):
        digest=hashlib.sha256(data).hexdigest();path=self.artifacts/digest;path.write_bytes(data);path.chmod(0o600);return digest

    def register_pair(self,doc=None):
        return pair.register(self.dest,doc or self.manifest,self.media,self.texts,self.artifacts,collector=lambda _:self.rows)

    def video(self,platform):
        return r.p.Video(self.media,self.source_sha,self.media.stat().st_size,r.p.file_stamp(self.media),31,'verified_local_profile',None if platform=='instagram' else 'https://scontent-example.cdninstagram.com/video.mp4?private=synthetic')

    def review_doc(self,platform):
        target=next(i for i in self.manifest['candidates'] if i['platform']==platform)
        video=self.video(platform)
        doc={'version':2,'runtime_id':'pair-test','review_id':'review-'+platform,'approval_record':'explicit-current-user-approval','reviewer':'synthetic-operator',
             'candidate':{'platform':platform,'account_id':target['account_id'],'job_id':target['job_id'],'media_sha256':self.source_sha,
                          'content_hash':r.p.content_hash(self.texts[platform].read_text(),video),'text_sha256':target['text_sha256'],'source_record':'slack-source','ai_generated':False},
             'history':copy.deepcopy(self.rows),'comparisons':[],'delivery':None}
        for row in self.rows:
            paired=platform=='threads' and row['post_id']=='301'
            doc['comparisons'].append({**{k:row[k] for k in ('platform','account_id','post_id')},'source_kind':'official_public_reencoding',
                'artifact_sha256':self.artifact(self.media.read_bytes() if paired else ('old-'+row['post_id']).encode()),'receipt_source_record':'official-get',
                'comparison_record':'full-review','report_sha256':self.artifact(('full comparison '+row['post_id']).encode()),'complete':True,
                'verdict':'authorized_cross_platform' if paired else 'distinct','paired_job_id':'pair-instagram' if paired else None})
        if platform=='threads':
            doc['delivery']={'account_id':'999','post_id':'301','job_id':'pair-instagram','source_sha256':self.source_sha,'url_sha256':review.digest(video.url),
                             'remote_sha256':self.source_sha,'bytes':video.size,'inspection_sha256':'d'*64,'expires_at':r.time.time()+1200}
        return doc

    def approve(self,platform,pair_id='one-approved-pair',doc=None):
        return review.register(self.dest,doc or self.review_doc(platform),self.media,self.texts[platform],self.artifacts,collector=lambda _:self.rows,pair_id=pair_id)

    def publisher(self,platform,ledger,client):
        return r.p.Publisher(platform,'999' if platform=='instagram' else '123',client,ledger,candidate_pair_id='one-approved-pair',
                             instagram_login_mode='unified' if platform=='instagram' else None,instagram_upload_mode='resumable' if platform=='instagram' else None)

    def finish(self,platform):
        ledger=r.p.Ledger(self.dest);client=mock.Mock();account='999' if platform=='instagram' else '123'
        published='301' if platform=='instagram' else '401';container='800' if platform=='instagram' else '900'
        url='https://www.instagram.com/reel/new/' if platform=='instagram' else 'https://www.threads.com/@synthetic/post/new'
        def request(method,path,*args,**kwargs):
            if path=='/me/assigned_instagram_accounts':return {'data':[{'id':'999'}]}
            if path=='/me':return {'id':'123'}
            if path=='/'+account+('/media' if platform=='instagram' else '/threads'):return {'id':container,'uri':r.p.INSTAGRAM_UPLOAD_BASE+'/'+container}
            if path=='/'+container:return {'status_code':'FINISHED','status':'FINISHED'}
            if path=='/'+account+('/media_publish' if platform=='instagram' else '/threads_publish'):return {'id':published}
            if path=='/'+published:return {'id':published,'permalink':url}
            raise AssertionError(path)
        client.request.side_effect=request;client.upload_instagram_video.return_value={'success':True}
        try:
            engine=self.publisher(platform,ledger,client)
            with mock.patch.object(review,'collect',side_effect=lambda *a:self.rows):
                self.assertEqual(engine.publish('pair-'+platform,self.texts[platform].read_text(),self.video(platform))['state'],'media_processing')
                ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?',('pair-'+platform,))
                result=engine.publish('pair-'+platform,self.texts[platform].read_text(),self.video(platform))
                self.assertEqual(result['state'],'published')
            self.rows.append({'platform':platform,'account_id':account,'post_id':published,'published_at':r.time.time(),'permalink':url,'media_type':'VIDEO','text_sha256':review.file_digest(self.texts[platform])})
            return result
        finally:ledger.close()

    def test_exact_pair_finishes_ig_second_then_threads_third_preserving_gap(self):
        self.register_pair();self.approve('instagram');self.finish('instagram')
        self.approve('threads');self.finish('threads')
        ledger=r.p.Ledger(self.dest)
        try:
            self.assertFalse(json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])['inventory_complete'])
            self.assertEqual(ledger.get(('threads','123','legacy-unknown'))['state'],'unknown')
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM jobs WHERE state='published'").fetchone()[0],5)
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM recovery_reviews WHERE state='consumed'").fetchone()[0],2)
        finally:ledger.close()
        with self.assertRaises(sqlite3.IntegrityError):
            # History changes already block replay; use original collector only to exercise singleton below.
            ledger=r.p.Ledger(self.dest)
            try:ledger.db.execute("INSERT INTO candidate_pair SELECT 1,pair_id,document,digest,legacy,created_at FROM candidate_pair")
            finally:ledger.close()

    def test_default_path_stays_blocked(self):
        self.register_pair()
        with self.assertRaises(r.p.SafeError):self.approve('instagram',pair_id=None)
        self.approve('instagram');ledger=r.p.Ledger(self.dest)
        try:
            with self.assertRaises(r.p.SafeError):ledger.claim(('instagram','999','pair-instagram'),'a'*64,auth_mode='unified',upload_mode='resumable',media_sha256=self.source_sha)
            with mock.patch.object(review,'collect',return_value=self.rows):
                with self.assertRaises(r.p.SafeError):ledger.prepare_review(('instagram','999','pair-instagram'),mock.Mock(),video=self.video('instagram'),upload_mode='resumable')
        finally:ledger.close()

    def test_source_ordinal_slack_and_evidence_mismatch_rejected(self):
        cases=[]
        for field,value in [('candidate_never_attempted',False),('single_writer_confirmed',False),('evidence_sha256','0'*64)]:
            doc=copy.deepcopy(self.manifest);doc[field]=value;cases.append(doc)
        for field,value in [('sha256','0'*64),('slack_channel','DOTHER')]:
            doc=copy.deepcopy(self.manifest);doc['source'][field]=value;cases.append(doc)
        doc=copy.deepcopy(self.manifest);doc['candidates'][0]['ordinal']=4;cases.append(doc)
        doc=copy.deepcopy(self.manifest);doc['legacy_unresolved']=[];cases.append(doc)
        for doc in cases:
            with self.subTest(case=doc),self.assertRaises((r.p.SafeError,OSError)):self.register_pair(doc)

    def test_known_same_source_attempt_blocks_even_when_declared_legacy(self):
        ledger=r.p.Ledger(self.dest)
        try:
            item=json.loads(ledger.db.execute('SELECT document FROM recovery_legacy_attempts').fetchone()[0]);item['media_sha256']=self.source_sha
            ledger.db.execute('UPDATE recovery_legacy_attempts SET document=?',(review.canonical(item),))
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.register_pair()

    def test_new_unknown_or_changed_legacy_row_blocks(self):
        self.register_pair()
        ledger=r.p.Ledger(self.dest)
        try:ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state) VALUES('threads','123','new-unknown','unknown')")
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.approve('instagram')

    def test_changed_history_or_candidate_blocks(self):
        self.register_pair()
        for field,value in [('text_sha256','0'*64),('job_id','different-job'),('media_sha256','0'*64)]:
            doc=self.review_doc('instagram');doc['candidate'][field]=value
            with self.subTest(field=field),self.assertRaises(r.p.SafeError):self.approve('instagram',doc=doc)
        self.rows[0]['published_at']+=1
        with self.assertRaises(r.p.SafeError):self.approve('instagram')

    def test_threads_cannot_start_before_confirmed_instagram(self):
        self.register_pair()
        with self.assertRaises(r.p.SafeError):self.approve('threads')

    def test_unknown_candidate_response_blocks_pair_and_all_retries(self):
        self.register_pair();self.approve('instagram');ledger=r.p.Ledger(self.dest);client=mock.Mock()
        client.request.side_effect=[{'data':[{'id':'999'}]},r.p.ApiError()]
        try:
            engine=self.publisher('instagram',ledger,client)
            with mock.patch.object(review,'collect',return_value=self.rows):
                self.assertEqual(engine.publish('pair-instagram',self.texts['instagram'].read_text(),self.video('instagram'))['state'],'unknown')
                with self.assertRaises(r.p.SafeError):engine.publish('pair-instagram',self.texts['instagram'].read_text(),self.video('instagram'))
            self.assertEqual(sum(c.args[0]=='POST' for c in client.request.call_args_list),1)
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.approve('threads')

    def test_new_public_post_and_matching_legacy_content_are_blocked(self):
        self.register_pair()
        ledger=r.p.Ledger(self.dest)
        try:
            item=json.loads(ledger.db.execute('SELECT document FROM recovery_legacy_attempts').fetchone()[0])
            item['content_hash']=self.review_doc('instagram')['candidate']['content_hash']
            ledger.db.execute('UPDATE recovery_legacy_attempts SET document=?',(review.canonical(item),))
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.approve('instagram')
        self.rows.append({**self.rows[0],'post_id':'unexpected'})
        with self.assertRaises(r.p.SafeError):self.approve('instagram')

    def test_unknown_final_publish_is_never_retried_in_pair_route(self):
        self.register_pair();self.approve('instagram');ledger=r.p.Ledger(self.dest);client=mock.Mock()
        identity={'data':[{'id':'999'}]}
        client.request.side_effect=[identity,{'id':'800','uri':r.p.INSTAGRAM_UPLOAD_BASE+'/800'},identity,{'status_code':'FINISHED'},r.p.ApiError()]
        client.upload_instagram_video.return_value={'success':True}
        try:
            engine=self.publisher('instagram',ledger,client)
            with mock.patch.object(review,'collect',return_value=self.rows):
                engine.publish('pair-instagram',self.texts['instagram'].read_text(),self.video('instagram'))
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='pair-instagram'")
                self.assertEqual(engine.publish('pair-instagram',self.texts['instagram'].read_text(),self.video('instagram'))['state'],'unknown')
                with self.assertRaises(r.p.SafeError):engine.publish('pair-instagram',self.texts['instagram'].read_text(),self.video('instagram'))
            self.assertEqual(sum(c.args[1]=='/999/media_publish' for c in client.request.call_args_list),1)
        finally:ledger.close()

    def test_native_cli_explicit_pair_option_reaches_only_mock_local_upload(self):
        self.register_pair();self.approve('instagram')
        real_api=r.p.APIClient;calls=[]
        class FakeAPI(real_api):
            def __init__(self,platform,token,mode):self.instagram_login_mode=mode
            def request(self,method,path,*args,**kwargs):
                calls.append((method,path))
                if path=='/me/assigned_instagram_accounts':return {'data':[{'id':'999'}]}
                if path=='/999/media':return {'id':'800','uri':r.p.INSTAGRAM_UPLOAD_BASE+'/800'}
                raise AssertionError(path)
            def upload_instagram_video(self,container,uri,video):
                calls.append(('LOCAL_UPLOAD',container));return {'success':True}
        args=['publish','--platform','instagram','--instagram-login-mode','unified','--instagram-upload-mode','resumable',
              '--state-dir',str(self.dest),'--job-id','pair-instagram','--candidate-pair-id','one-approved-pair',
              '--text-file',str(self.texts['instagram']),'--video-file',str(self.media),'--execute']
        output=io.StringIO()
        with mock.patch.dict(os.environ,{'INSTAGRAM_EXPECTED_USER_ID':'999','INSTAGRAM_USER_ACCESS_TOKEN':'synthetic-token'}),             mock.patch.object(r.p,'APIClient',FakeAPI),mock.patch.object(r.p,'prepare_video',return_value=self.video('instagram')),             mock.patch.object(review,'collect',return_value=self.rows),redirect_stdout(output):
            self.assertEqual(r.p.main(args),3)
        self.assertEqual(json.loads(output.getvalue())['state'],'media_processing')
        self.assertEqual(sum(method=='LOCAL_UPLOAD' for method,path in calls),1)
        self.assertNotIn(('POST','/999/media_publish'),calls)

    def test_second_pair_and_concurrent_writer_rejected(self):
        self.register_pair()
        with self.assertRaises(sqlite3.IntegrityError):self.register_pair()
        ledger=r.p.Ledger(self.dest)
        try:
            with self.assertRaises(BlockingIOError):self.register_pair()
        finally:ledger.close()

if __name__=='__main__':unittest.main()
