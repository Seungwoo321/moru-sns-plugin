"""Synthetic app deletion through the actual native registration/publisher CLI."""
import copy
import json
import time
from unittest import mock
import unittest
import test_profile_recovery as base
import receipt_absence as absence

r=base.r
review=base.review
admission=base.admission


class DeletedReceiptTests(base.RecoveredProfileTests):
    def setup_deleted(self):
        ledger=r.p.Ledger(self.dest)
        row=dict(ledger.db.execute("SELECT * FROM jobs WHERE platform='instagram' AND post_id='201'").fetchone())
        ledger.db.execute('UPDATE jobs SET updated_at=? WHERE job_id=?',(time.time(),row['job_id']))
        self.doc['prior_jobs_sha256']=admission.jobs_digest(ledger,self.key)
        self.before=self.snapshot(ledger);ledger.close()
        self.doc['version']=2
        self.doc['external_absences']=[{k:row[k] for k in ('platform','account_id','job_id','post_id','permalink')}]
        self.doc['external_absences'][0].update(user_confirmation_record='explicit-app-deletion-and-immediate-replacement',
            report_sha256=self.artifact(b'Synthetic user-confirmed deletion, complete list and object GET observation'),immediate_replacement=True)
        self.rows=[i for i in self.rows if i['post_id']!='201']
        self.doc['history']=copy.deepcopy(self.rows)
        self.review_path.write_text(json.dumps(self.doc))
        provider=self.provider();original=provider.request;test=self
        def request(self,method,path,payload=None,params=None,**kw):
            if method=='GET' and path=='/999/media':
                self.calls.append((method,path,payload,params));return {'data':[]}
            if method=='GET' and path=='/201':
                self.calls.append((method,path,payload,params));raise r.p.ApiError(400,graph_code=100,graph_subcode=33)
            return original(self,method,path,payload,params,**kw)
        provider.request=request
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(absence.runtime,'load_environment',side_effect=lambda platform:test.config[platform]).start()
        mock.patch.object(r.p,'APIClient',provider).start()
        return provider

    def test_deleted_exact_receipt_new_candidate_and_get_only_resume(self):
        provider=self.setup_deleted()
        self.assertEqual(self.register(),0)
        self.assertTrue(all(c[0]=='GET' for c in provider.calls))
        self.assertEqual(self.invoke(provider)[1]['state'],'media_processing')
        ledger=r.p.Ledger(self.dest);ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?',(self.job,));ledger.close()
        self.assertEqual(self.invoke(provider)[1]['state'],'confirming_label')
        count=len(provider.calls)
        self.assertEqual(self.invoke(provider)[1]['state'],'published')
        self.assertTrue(all(c[0]=='GET' for c in provider.calls[count:]))
        self.assertEqual(sum(c[1]=='/999/media_publish' for c in provider.calls),1)
        ledger=r.p.Ledger(self.dest)
        self.assertEqual(self.before,self.snapshot(ledger));ledger.close()
        self.assertEqual(self.register(),2)

    def test_history_only_keeps_interval_and_never_reserves_deleted_receipt(self):
        provider = self.setup_deleted()
        self.doc['version'] = 4
        item = self.doc['external_absences'][0]
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(), 2)
        item['immediate_replacement'] = False
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(), 0)
        self.assertEqual(self.invoke(provider)[0], 2)
        self.assertEqual(provider.uploads, [])
        ledger = r.p.Ledger(self.dest)
        try:
            self.assertIsNone(ledger.get(self.key))
            self.assertIsNone(ledger.db.execute(
                'SELECT value FROM recovery_metadata WHERE key=?',
                (absence.reservation(item),),
            ).fetchone())
            self.assertEqual(self.before, self.snapshot(ledger))
        finally:
            ledger.close()

    def test_new_post_after_consumed_replacement_preserves_receipts(self):
        provider = self.setup_deleted()
        self.doc['version'] = 4
        item = self.doc['external_absences'][0]
        item['immediate_replacement'] = False
        ledger = r.p.Ledger(self.dest)
        try:
            ledger.db.execute('UPDATE jobs SET updated_at=? WHERE job_id=?',
                              (time.time() - 7 * 3600, item['job_id']))
            old_reservation = review.canonical(('instagram', '999', 'previous-replacement'))
            ledger.db.execute('INSERT INTO recovery_metadata VALUES (?,?)',
                              (absence.reservation(item), old_reservation))
            self.doc['prior_jobs_sha256'] = admission.jobs_digest(ledger, self.key)
            self.before = self.snapshot(ledger)
        finally:
            ledger.close()
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(), 0)
        self.assertEqual(self.invoke(provider)[1]['state'], 'media_processing')
        ledger = r.p.Ledger(self.dest)
        ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?', (self.job,))
        ledger.close()
        self.assertEqual(self.invoke(provider)[1]['state'], 'confirming_label')
        self.assertEqual(self.invoke(provider)[1]['state'], 'published')
        self.assertEqual(sum(c[1] == '/999/media_publish' for c in provider.calls), 1)
        ledger = r.p.Ledger(self.dest)
        try:
            self.assertEqual(self.before, self.snapshot(ledger))
            self.assertEqual(ledger.db.execute(
                'SELECT value FROM recovery_metadata WHERE key=?',
                (absence.reservation(item),),
            ).fetchone()[0], old_reservation)
        finally:
            ledger.close()

    def test_other_account_partial_list_and_permission_failure_block(self):
        provider=self.setup_deleted();original=provider.request
        for scenario in ('account','partial','permission','exists'):
            def request(client,method,path,payload=None,params=None,**kw):
                if scenario=='account' and path=='/me/assigned_instagram_accounts':return {'data':[{'id':'998','username':'synthetic'}]}
                if scenario=='partial' and path=='/999/media':return {'data':[],'paging':{'next':'untrusted-pagination'}}
                if path=='/201' and scenario=='permission':raise r.p.ApiError(403)
                if path=='/201' and scenario=='exists':return {'id':'201'}
                return original(client,method,path,payload,params,**kw)
            with mock.patch.object(provider,'request',request):self.assertEqual(self.register(),2,scenario)
        self.assertTrue(all(c[0]=='GET' for c in provider.calls))

    def test_other_recent_receipt_and_unresolved_candidate_are_not_exempt(self):
        provider=self.setup_deleted()
        ledger=r.p.Ledger(self.dest)
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state,post_id,permalink,updated_at) VALUES('instagram','999','other-recent','published','202','https://www.instagram.com/reel/other/',?)",(time.time(),))
        self.doc['prior_jobs_sha256']=admission.jobs_digest(ledger,self.key);ledger.close()
        self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(),2)
        ledger=r.p.Ledger(self.dest)
        # This row is test-only: exercise active candidate rejection in a separate fixture state.
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state,content_hash) VALUES('instagram','999',?,'unknown',?)",(self.job,self.digest));ledger.close()
        self.assertEqual(self.register(),2)
        self.assertTrue(all(c[0]=='GET' for c in provider.calls))

    def test_reuse_for_different_candidate_and_history_reappearance_block(self):
        provider=self.setup_deleted();self.assertEqual(self.register(),0)
        ledger=r.p.Ledger(self.dest)
        with self.assertRaises(r.p.SafeError):absence.validate(ledger,self.doc['external_absences'],('instagram','999','capture-'+'a'*64))
        ledger.close()
        original=provider.request
        def request(client,method,path,payload=None,params=None,**kw):
            if path=='/201':return {'id':'201'}
            return original(client,method,path,payload,params,**kw)
        with mock.patch.object(provider,'request',request):self.assertEqual(self.invoke(provider)[0],2)
        self.assertEqual(provider.uploads,[])
        self.assertTrue(all(c[0]=='GET' for c in provider.calls))

    def test_other_receipt_six_hour_claim_guard_remains_atomic(self):
        provider=self.setup_deleted();original=provider.request
        item={'id':'202','timestamp':'2026-11-22T00:00:00Z','media_type':'VIDEO',
              'caption':'Other approved material','permalink':'https://www.instagram.com/reel/other/'}
        self.rows.append(review.receipt('instagram','999',item))
        ledger=r.p.Ledger(self.dest)
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state,post_id,permalink,updated_at) VALUES('instagram','999','other-recent','published','202',?,?)",(item['permalink'],time.time()))
        self.doc['prior_jobs_sha256']=admission.jobs_digest(ledger,self.key);ledger.close()
        self.doc['history']=copy.deepcopy(self.rows);self.review_path.write_text(json.dumps(self.doc))
        def request(client,method,path,payload=None,params=None,**kw):
            if method=='GET' and path=='/999/media':return {'data':[item]}
            return original(client,method,path,payload,params,**kw)
        with mock.patch.object(provider,'request',request):
            self.assertEqual(self.register(),0)
            self.assertEqual(self.invoke(provider)[0],2)
        ledger=r.p.Ledger(self.dest);self.assertIsNone(ledger.get(self.key));ledger.close()
        self.assertEqual(provider.uploads,[])
        self.assertTrue(all(c[0]=='GET' for c in provider.calls))

    def test_numeric_api_error_classification_never_retains_response_text(self):
        import io
        import urllib.error
        client=r.p.APIClient('instagram','synthetic-token','unified')
        error=urllib.error.HTTPError('https://example.invalid',400,'private provider text',{},
            io.BytesIO(b'{"error":{"code":100,"error_subcode":33,"message":"private signed URL"}}'))
        # setup_deleted is not active here, so the real API client uses a mocked opener.
        client.opener=mock.Mock();client.opener.open.side_effect=error
        with self.assertRaises(r.p.ApiError) as caught:client.request('GET','/201')
        self.assertEqual((caught.exception.status,caught.exception.graph_code,caught.exception.graph_subcode),(400,100,33))
        self.assertEqual(str(caught.exception),'API request failed.')

    def test_other_platform_absence_is_observation_only(self):
        provider=self.setup_deleted();original=provider.request
        ledger=r.p.Ledger(self.dest)
        old=dict(ledger.db.execute("SELECT * FROM jobs WHERE platform='threads' AND post_id='102'").fetchone());ledger.close()
        observation={k:old[k] for k in ('platform','account_id','job_id','post_id','permalink')}
        observation.update(user_confirmation_record='explicit-app-deletion',report_sha256=self.artifact(b'Synthetic Threads deletion observation'),immediate_replacement=False)
        self.doc['external_absences'].append(observation)
        self.rows=[i for i in self.rows if i['post_id']!='102'];self.doc['history']=copy.deepcopy(self.rows)
        self.review_path.write_text(json.dumps(self.doc))
        def request(client,method,path,payload=None,params=None,**kw):
            if path=='/me':return {'id':'123','username':'synthetic'}
            if path=='/123/threads':return {'data':[{'id':'101','timestamp':'2026-11-22T00:00:00Z','media_type':'VIDEO','text':'Old material','permalink':'https://www.threads.com/@synthetic/post/one'}]}
            if path=='/102':raise r.p.ApiError(400,graph_code=100,graph_subcode=33)
            return original(client,method,path,payload,params,**kw)
        with mock.patch.object(provider,'request',request):self.assertEqual(self.register(),0)
        ledger=r.p.Ledger(self.dest)
        self.assertIsNone(ledger.db.execute('SELECT value FROM recovery_metadata WHERE key=?',(absence.reservation(observation),)).fetchone())
        self.assertEqual(self.before,self.snapshot(ledger));ledger.close()

    def test_deleted_replacement_lost_post_response_stays_unknown(self):
        provider=self.setup_deleted();self.assertEqual(self.register(),0);original=provider.request
        def request(client,method,path,payload=None,params=None,**kw):
            if path=='/999/media_publish':
                client.calls.append((method,path,payload,params));raise r.p.ApiError()
            return original(client,method,path,payload,params,**kw)
        with mock.patch.object(provider,'request',request):
            self.assertEqual(self.invoke(provider)[1]['state'],'media_processing')
            ledger=r.p.Ledger(self.dest);ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?',(self.job,));ledger.close()
            self.assertEqual(self.invoke(provider)[1]['state'],'unknown')
            count=len(provider.calls)
            self.assertEqual(self.invoke(provider)[1]['state'],'unknown')
            self.assertEqual(len(provider.calls),count)
        self.assertEqual(len(provider.uploads),1)
        self.assertEqual(sum(c[1]=='/999/media_publish' for c in provider.calls),1)

    def test_v017_unobserved_deletion_stays_blocked(self):
        self.setup_deleted();self.doc['version']=1;del self.doc['external_absences'];self.review_path.write_text(json.dumps(self.doc))
        self.assertEqual(self.register(),2)


if __name__=='__main__':unittest.main()
