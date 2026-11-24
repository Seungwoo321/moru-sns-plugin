"""Existing recovered journal + one app-deleted Threads receipt + exact Blob candidate."""
import contextlib
import copy
import io
import json
import os
import time
import unittest
from unittest import mock
import test_profile_recovery as base
import test_blob_publish as native
import blob_publish as blob
import profile_recovery as admission
import publication_profile as profiles
import candidate_review as review
import publisher as p


class RecoveredBlobTests(unittest.TestCase):
    write=base.RecoveredProfileTests.write
    artifact=base.RecoveredProfileTests.artifact
    snapshot=base.RecoveredProfileTests.snapshot
    sdk_call=native.NativeBlobTests.sdk_call
    fetch=native.NativeBlobTests.fetch
    invoke=native.NativeBlobTests.invoke
    due=native.NativeBlobTests.due

    def setUp(self):
        base.RecoveredProfileTests.setUp(self)
        self.state=self.dest;self.video=self.video_path;self.text=self.caption
        self.receipts=self.root/'blob-receipts';self.receipts.mkdir(mode=0o700)
        self.host='synthetic.private.blob.vercel-storage.com'
        self.config=self.write('blob-config.json',json.dumps({'version':1,'enabled':True,'store_host':self.host,
            'receipt_dir':str(self.receipts),'approved_slack_channels':['DSYNTHETIC']}).encode())
        approved=profiles.bind(self.profile,str(self.evidence),str(self.video),'threads',self.text.read_text(),self.media)
        self.key=('threads','123',self.job)
        self.digest=p.content_hash(self.text.read_text(),self.media,publication_binding=approved['binding_sha256'])
        blobdoc={'version':1,'platform':'threads','account_id':'123','job_id':self.job,'media_sha256':self.media.sha256,
            'content_sha256':p.content_hash(self.text.read_text(),self.media),'slack_channel':'DSYNTHETIC',
            'slack_file_id':'FSYNTHETIC','slack_message_ts':'1.000001','approval_record':'synthetic-current-replacement',
            'final_original':True,'ai_generated':True}
        self.approval=self.write('blob-approval.json',json.dumps(blobdoc).encode())
        ledger=p.Ledger(self.dest)
        old=dict(ledger.db.execute("SELECT * FROM jobs WHERE platform='threads' AND post_id='102'").fetchone())
        ledger.db.execute('UPDATE jobs SET updated_at=? WHERE platform=? AND job_id=?',(time.time(),'threads',old['job_id']))
        self.doc.update(version=3,platform='threads',account_id='123',content_hash=self.digest,
            profile_binding_sha256=approved['binding_sha256'],prior_jobs_sha256=admission.jobs_digest(ledger,self.key))
        self.before=self.snapshot(ledger);ledger.close()
        self.rows=[r for r in self.rows if r['post_id']!='102'];self.doc['history']=copy.deepcopy(self.rows)
        deleted={k:old[k] for k in ('platform','account_id','job_id','post_id','permalink')}
        deleted.update(user_confirmation_record='synthetic-app-delete-and-one-replacement',
            report_sha256=self.artifact(b'Synthetic explicit app deletion, complete public list and absence review'),immediate_replacement=True)
        self.doc['external_absences']=[deleted]
        self.doc['transport']={'kind':'vercel_private_blob','store_host':self.host,
            'config_sha256':review.digest(review.canonical(json.loads(self.config.read_text()))),
            'approval_sha256':review.digest(review.canonical(blobdoc))}
        self.review_path.write_text(json.dumps(self.doc))
        self.env={'MORU_PUBLICATION_PROFILE':str(self.profile_path),'MORU_PRIVATE_BLOB_CONFIG':str(self.config),
            'MORU_BLOB_READ_WRITE_TOKEN':'synthetic-blob-secret','THREADS_EXPECTED_USER_ID':'123','THREADS_USER_ACCESS_TOKEN':'synthetic-meta-secret'}
        self.profile_flags=['--source-file',str(self.video),'--publication-evidence',str(self.evidence),'--reviewed-hash',self.digest]
        self.sdk=[];self.api=[];self.delete_fail=False;self.put_fail=False;self.post_fail=False;self.fetch_fail=False
        outer=self
        class Provider:
            def __init__(self,*a,**kw):pass
            def request(self,method,path,payload=None,params=None):
                outer.api.append((method,path,payload))
                if path=='/me':return {'id':'123','username':'synthetic'}
                if path=='/123/threads' and method=='GET':return {'data':[{'id':'101','timestamp':'2026-11-22T00:00:00Z','media_type':'VIDEO','text':'Old material','permalink':'https://www.threads.com/@synthetic/post/one'}]}
                if path=='/102':raise p.ApiError(400,graph_code=100,graph_subcode=33)
                if path=='/123/threads':return {'id':'700'}
                if path=='/700':return {'status':'FINISHED'}
                if path=='/123/threads_publish':
                    if outer.post_fail:raise p.ApiError()
                    return {'id':'900'}
                if path=='/900':return {'id':'900','media_type':'VIDEO','permalink':'https://www.threads.com/@synthetic/post/new'}
                raise AssertionError(path)
        self.Provider=Provider
        collect=mock.patch.object(review,'collect',side_effect=lambda *a,**kw:self.rows);collect.start();self.addCleanup(collect.stop)
        config=mock.patch.object(blob.review.runtime,'load_environment',return_value={'expected_user_id':'123','expected_username':'synthetic','instagram_login_mode':None,'access_token':'synthetic'});config.start();self.addCleanup(config.stop)

    def register(self):
        with mock.patch.dict(os.environ,self.env,clear=True),mock.patch.object(p,'APIClient',self.Provider),contextlib.redirect_stdout(io.StringIO()):
            return admission.main(['--state-dir',str(self.dest),'--platform','threads','--review',str(self.review_path),
                '--source-file',str(self.video),'--video-file',str(self.video),'--text-file',str(self.text),
                '--publication-evidence',str(self.evidence),'--artifacts-dir',str(self.artifacts),'--blob-approval',str(self.approval)])

    def test_actual_recovered_entrypoint_replacement_and_preserved_instagram(self):
        self.assertEqual(self.register(),0);self.assertEqual(self.invoke()[1]['status'],'media_processing');self.due()
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.sdk.count('delete'),1)
        ledger=p.Ledger(self.dest)
        self.assertEqual(self.before,self.snapshot(ledger));self.assertEqual(ledger.get(self.key)['state'],'published');ledger.close()
        count=len(self.api);self.assertEqual(self.invoke()[1]['status'],'cleaned');self.assertEqual(len(self.api),count)
        self.assertEqual(self.register(),2)

    def test_unregistered_or_changed_transport_cannot_upload(self):
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk,[])
        self.assertEqual(self.register(),0)
        c=json.loads(self.config.read_text());c['store_host']='other.private.blob.vercel-storage.com';self.config.write_text(json.dumps(c))
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk,[])

    def test_unknown_publish_and_cleanup_retry_keep_existing_guards(self):
        self.assertEqual(self.register(),0);self.invoke();self.due();self.post_fail=True
        self.assertEqual(self.invoke()[1]['status'],'unknown');count=len(self.api)
        self.assertEqual(self.invoke()[1]['status'],'publication_unknown')
        self.assertTrue(all(c[0]=='GET' for c in self.api[count:]));self.assertNotIn('delete',self.sdk)
        ledger=p.Ledger(self.dest);self.assertEqual(self.before,self.snapshot(ledger));ledger.close()

    def test_observation_only_does_not_waive_interval_or_consume_replacement(self):
        self.doc['external_absences'][0]['immediate_replacement']=False
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(),0)
        # Complete public history alone cannot waive the preserved recent receipt.
        self.assertEqual(self.invoke()[0],2)
        self.assertEqual(self.sdk,[])
        self.assertFalse(any(c[0]=='POST' for c in self.api))
        ledger=p.Ledger(self.dest)
        self.assertIsNone(ledger.get(self.key))
        self.assertFalse(ledger.db.execute("SELECT 1 FROM recovery_metadata WHERE key LIKE 'external_absence:%'").fetchone())
        ledger.close()

    def test_ordinary_candidate_after_interval_uses_observation_without_exception(self):
        self.doc['external_absences'][0]['immediate_replacement']=False
        ledger=p.Ledger(self.dest)
        ledger.db.execute("UPDATE jobs SET updated_at=? WHERE platform='threads' AND post_id='102'",(time.time()-7*3600,))
        self.doc['prior_jobs_sha256']=admission.jobs_digest(ledger,self.key)
        self.before=self.snapshot(ledger);ledger.close()
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(),0)
        self.assertEqual(self.invoke()[1]['status'],'media_processing');self.due()
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        ledger=p.Ledger(self.dest)
        self.assertEqual(self.before,self.snapshot(ledger))
        self.assertFalse(ledger.db.execute("SELECT 1 FROM recovery_metadata WHERE key LIKE 'external_absence:%'").fetchone())
        ledger.close()
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.sdk.count('delete'),1)

    def test_changed_history_or_new_unknown_blocks_before_blob(self):
        self.assertEqual(self.register(),0)
        self.rows[0]['permalink']='https://www.threads.com/@synthetic/post/changed'
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk,[])
        self.rows=copy.deepcopy(self.doc['history'])
        ledger=p.Ledger(self.dest)
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,content_hash,state,updated_at) VALUES('threads','123','new-unknown','different','unknown',?)",(time.time(),))
        ledger.close()
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk,[])

    def test_cleanup_failure_retry_does_not_publish_again(self):
        self.assertEqual(self.register(),0);self.invoke();self.due();self.delete_fail=True
        self.assertEqual(self.invoke()[1]['status'],'cleanup_pending');count=len(self.api)
        self.delete_fail=False;self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertTrue(all(c[0]=='GET' for c in self.api[count:]));self.assertEqual(self.sdk.count('put'),1)

if __name__=='__main__':unittest.main()
