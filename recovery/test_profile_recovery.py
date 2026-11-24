import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest import mock

import reconcile as r
import candidate_review as review
import publication_profile as profiles
import profile_recovery as admission
import test_candidate_pair as base


class RecoveredProfileTests(unittest.TestCase):
    write=base.CandidatePairTests.write
    artifact=base.CandidatePairTests.artifact

    def setUp(self):
        base.CandidatePairTests.setUp(self)
        # Real MP4 operational fixture exercises the actual new-delivery guard.
        self.original_delivery_gate.stop()
        self.video_path=self.root/'new-english-capture.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=320x240:r=24',
                        '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','31','-c:v','libx264','-pix_fmt','yuv420p',
                        '-c:a','aac','-b:a','128k','-use_editlist','0','-movflags','+faststart',str(self.video_path)],check=True,capture_output=True)
        self.video_path.chmod(0o600)
        self.media=r.p.prepare_video('instagram',str(self.video_path),ai_generated=True,allow_missing_url=True)
        self.caption=self.write('new-caption.txt',b'A newly captured English game workspace.')
        self.profile={'version':1,'profile_id':'synthetic-work','language':'en','require_fresh_capture':True,
                      'max_capture_age_hours':24,'ai_disclosure':True,'check_interval_hours':12,'minimum_publish_interval_hours':6}
        self.profile_path=self.write('profile.json',json.dumps(self.profile).encode())
        evidence={'version':1,'profile_id':'synthetic-work','source_kind':'video','source_sha256':self.media.sha256,
                  'media_sha256':self.media.sha256,'caption_sha256':review.file_digest(self.caption),'title':'English game UI',
                  'ui_language':'en','subtitle_language':'none','title_language':'en','caption_language':'en',
                  'captured_at':time.time(),'fresh_capture':True,'review_record':'synthetic reviewed capture provenance'}
        self.evidence=self.write('capture-review.json',json.dumps(evidence).encode())
        approval=profiles.bind(self.profile,str(self.evidence),str(self.video_path),'instagram',self.caption.read_text(),self.media)
        self.job=approval['job_id'];self.key=('instagram','999',self.job)
        self.digest=r.p.content_hash(self.caption.read_text(),self.media,publication_binding=approval['binding_sha256'])
        ledger=r.p.Ledger(self.dest)
        try:
            ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,content_hash,state,updated_at,auth_mode,upload_mode) VALUES('instagram','999','old-failed',?,'failed',?,'unified','resumable')",('e'*64,time.time()-90000))
            ledger.db.execute("INSERT INTO recovery_media VALUES('instagram',?,'old-failed')",('f'*64,))
            self.before=self.snapshot(ledger)
            self.doc={'version':1,'runtime_id':'pair-test','platform':'instagram','account_id':'999','job_id':self.job,
                      'source_sha256':self.media.sha256,'media_sha256':self.media.sha256,'profile_binding_sha256':approval['binding_sha256'],
                      'content_hash':self.digest,'base_content_hash':r.p.content_hash(self.caption.read_text(),self.media),'history':copy.deepcopy(self.rows),'prior_jobs_sha256':admission.jobs_digest(ledger,self.key),
                      'legacy_attempts_sha256':admission.legacy_digest(ledger),'candidate_never_attempted':True,'single_writer_confirmed':True,
                      'approval_record':'synthetic-operator-approval','distinctness_report_sha256':self.artifact(b'Synthetic full comparison of all prior published media and attempts'),
                      'pending_exclusions':[{'platform':'threads','account_id':'123','job_id':'legacy-unknown',
                                             'report_sha256':self.artifact(b'Synthetic exact candidate excluded from prior unrelated unknown attempt')}]}
        finally:ledger.close()
        self.review_path=self.write('native-approval.json',json.dumps(self.doc).encode())
        self.env={'MORU_PUBLICATION_PROFILE':str(self.profile_path),'INSTAGRAM_EXPECTED_USER_ID':'999','INSTAGRAM_USER_ACCESS_TOKEN':'synthetic-only'}

    def snapshot(self,ledger):
        return {'jobs':[dict(r) for r in ledger.db.execute('SELECT * FROM jobs ORDER BY platform,account_id,job_id') if r['job_id']!=self.job],
                'legacy':admission.legacy_digest(ledger),'provenance':ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0],
                'schema':list(ledger.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"))}

    def register(self):
        with mock.patch.dict(os.environ,self.env),mock.patch.object(review,'collect',return_value=self.rows),contextlib.redirect_stdout(io.StringIO()):
            return admission.main(['--state-dir',str(self.dest),'--review',str(self.review_path),'--source-file',str(self.video_path),
                                   '--video-file',str(self.video_path),'--text-file',str(self.caption),'--publication-evidence',str(self.evidence),
                                   '--artifacts-dir',str(self.artifacts)])

    def invoke(self,provider):
        output=io.StringIO()
        args=['publish','--platform','instagram','--instagram-login-mode','unified','--instagram-upload-mode','resumable',
              '--state-dir',str(self.dest),'--job-id',self.job,'--text-file',str(self.caption),'--video-file',str(self.video_path),
              '--source-file',str(self.video_path),'--publication-evidence',str(self.evidence),'--reviewed-hash',self.digest,'--execute']
        with mock.patch.dict(os.environ,self.env),mock.patch.object(review,'collect',return_value=self.rows),mock.patch.object(r.p,'APIClient',provider),contextlib.redirect_stdout(output):
            code=r.p.main(args)
        return code,json.loads(output.getvalue())

    def provider(self):
        test=self;original=r.p.APIClient
        class Provider(original):
            calls=[];uploads=[];labels=[False,True]
            def __init__(self,*a,**kw):
                super().__init__(*a,**kw)
                self.opener=mock.Mock();self.opener.open.side_effect=self.upload
            def upload(self,request,**kw):
                test.assertEqual(request.full_url,r.p.INSTAGRAM_UPLOAD_BASE+'/700')
                data=b''
                while True:
                    chunk=request.data.read(65536)
                    if not chunk:break
                    data+=chunk
                test.assertEqual(hashlib.sha256(data).hexdigest(),test.media.sha256)
                self.uploads.append(len(data))
                response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'{"success":true}'
                return response
            def request(self,method,path,payload=None,params=None,**kw):
                self.calls.append((method,path,payload,params))
                if path=='/me/assigned_instagram_accounts':return {'data':[{'id':'999','username':'synthetic'}]}
                if path=='/999/media':
                    test.assertEqual(payload['is_ai_generated'],'true');test.assertEqual(payload['media_type'],'REELS')
                    test.assertEqual(payload['upload_type'],'resumable');test.assertNotIn('video_url',payload)
                    return {'id':'700','uri':r.p.INSTAGRAM_UPLOAD_BASE+'/700'}
                if path=='/700':return {'status_code':'FINISHED'}
                if path=='/999/media_publish':return {'id':'901'}
                if path=='/901':
                    test.assertIn('media_product_type',params['fields'])
                    return {'id':'901','media_type':'VIDEO','media_product_type':'REELS','is_ai_generated':self.labels.pop(0),
                            'permalink':'https://www.instagram.com/reel/new-capture/'}
                raise AssertionError(path)
        return Provider

    def test_native_work_recovered_ledger_upload_label_and_preservation(self):
        self.assertEqual(self.register(),0)
        provider=self.provider()
        self.assertEqual(self.invoke(provider)[1]['state'],'media_processing')
        ledger=r.p.Ledger(self.dest)
        ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?',(self.job,));ledger.close()
        self.assertEqual(self.invoke(provider)[1]['state'],'confirming_label')
        count=len(provider.calls)
        self.assertEqual(self.invoke(provider)[1]['state'],'published')
        self.assertTrue(all(c[0]=='GET' for c in provider.calls[count:]))
        self.assertEqual(len(provider.uploads),1)
        self.assertEqual(sum(c[1]=='/999/media_publish' for c in provider.calls),1)
        ledger=r.p.Ledger(self.dest)
        try:
            self.assertEqual(self.before,self.snapshot(ledger))
            self.assertEqual(ledger.db.execute("SELECT job_id FROM recovery_media WHERE platform='instagram' AND sha256=?",(self.media.sha256,)).fetchone()[0],self.job)
            self.assertEqual(ledger.db.execute("SELECT job_id FROM recovery_media WHERE platform='instagram' AND sha256=?",('f'*64,)).fetchone()[0],'old-failed')
            self.assertFalse(json.loads(self.before['provenance'])['inventory_complete'])
        finally:ledger.close()
        before=len(provider.calls)
        self.assertEqual(self.invoke(provider)[1]['state'],'published')
        self.assertEqual(len(provider.calls),before)
        self.assertEqual(self.register(),2)

    def test_uncertain_actual_publish_receipt_cannot_retry_or_reupload(self):
        self.assertEqual(self.register(),0);provider=self.provider();original=provider.request
        def lost(self,method,path,payload=None,params=None,**kw):
            if path=='/999/media_publish':
                self.calls.append((method,path,payload,params));raise r.p.ApiError()
            return original(self,method,path,payload,params,**kw)
        with mock.patch.object(provider,'request',lost):
            self.assertEqual(self.invoke(provider)[1]['state'],'media_processing')
            ledger=r.p.Ledger(self.dest)
            ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?',(self.job,));ledger.close()
            self.assertEqual(self.invoke(provider)[1]['state'],'unknown')
            count=len(provider.calls)
            self.assertEqual(self.invoke(provider)[1]['state'],'unknown')
        self.assertEqual(len(provider.calls),count);self.assertEqual(len(provider.uploads),1)
        self.assertEqual(sum(c[1]=='/999/media_publish' for c in provider.calls),1)

    def test_snapshot_is_read_only_and_binds_existing_jobs(self):
        before={p.name:p.read_bytes() for p in self.dest.iterdir() if p.is_file()}
        with mock.patch.object(review,'collect',return_value=self.rows):
            result=admission.snapshot(self.dest,'instagram',self.job)
        self.assertEqual(result['prior_jobs_sha256'],self.doc['prior_jobs_sha256'])
        self.assertEqual(result['state_writes'],0)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.dest.iterdir() if p.is_file()})

    def test_same_source_claim_and_changed_history_block_without_post(self):
        ledger=r.p.Ledger(self.dest)
        ledger.db.execute("INSERT INTO recovery_media VALUES('instagram',?,'known-prior')",(self.media.sha256,));ledger.close()
        self.assertEqual(self.register(),2)
        self.assertEqual(self.invoke(self.provider())[0],2)

    def test_approval_does_not_hide_changed_or_new_uncertain_attempt(self):
        self.assertEqual(self.register(),0)
        ledger=r.p.Ledger(self.dest)
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state) VALUES('instagram','999','new-unknown','unknown')");ledger.close()
        provider=self.provider();self.assertEqual(self.invoke(provider)[0],2);self.assertEqual(provider.calls,[])

    def test_failed_base_content_and_wrong_account_are_not_hidden_by_profile_hash(self):
        ledger=r.p.Ledger(self.dest)
        ledger.db.execute("UPDATE jobs SET content_hash=? WHERE job_id='old-failed'",(self.doc['base_content_hash'],))
        self.doc['prior_jobs_sha256']=admission.jobs_digest(ledger,self.key);ledger.close()
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(),2)
        self.doc['account_id']='998';self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(),2)

    def test_official_history_drift_blocks_before_upload(self):
        self.assertEqual(self.register(),0)
        self.rows[0]['published_at']+=1
        provider=self.provider();self.assertEqual(self.invoke(provider)[0],2)
        self.assertEqual(provider.calls,[]);self.assertEqual(provider.uploads,[])

    def test_threads_route_is_held_without_delivery_registration(self):
        ledger=r.p.Ledger(self.dest)
        try:
            marker=r.p.read_state_marker(self.dest/'runtime.json')
            with self.assertRaisesRegex(r.p.SafeError,'delivery proof is not registered'):
                admission.require_route('threads',marker,self.media)
        finally:ledger.close()

if __name__=='__main__':unittest.main()
