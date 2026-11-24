import hashlib
import json
import time
import unittest
from unittest import mock

import publisher as p
import publication_profile as profile
import test_publisher as fixture
import test_media_policy as policy_tests


class ProfileTests(unittest.TestCase):
    setUpBase=fixture.PublisherTests.setUp
    tearDown=fixture.PublisherTests.tearDown
    video=fixture.PublisherTests.video
    cli=policy_tests.MediaPolicyTests.cli

    def setUp(self):
        self.setUpBase()
        self.asset=self.video('threads',ai=True);self.asset.path.chmod(0o600)
        from dataclasses import replace
        self.asset=replace(self.asset,stamp=p.file_stamp(self.asset.path))
        self.profile_job='capture-'+self.asset.sha256
        self.profile_path=self.state.parent/'profile.json'
        self.evidence_path=self.state.parent/'evidence.json'
        self.profile={'version':1,'profile_id':'synthetic-profile','language':'en',
                      'require_fresh_capture':True,'max_capture_age_hours':24,'ai_disclosure':True,
                      'check_interval_hours':12,'minimum_publish_interval_hours':6}
        self.evidence={'version':1,'profile_id':'synthetic-profile','source_kind':'video',
                       'source_sha256':self.asset.sha256,'media_sha256':self.asset.sha256,
                       'caption_sha256':hashlib.sha256(b'Approved synthetic caption').hexdigest(),
                       'title':'Game workspace','ui_language':'en','subtitle_language':'en',
                       'title_language':'en','caption_language':'en','captured_at':time.time(),
                       'fresh_capture':True,'review_record':'synthetic full UI and subtitle review'}
        self.save()
        self.flags=['--approved-source-kind','video','--video-file',str(self.asset.path),
                    '--video-url',self.asset.url,'--publication-evidence',str(self.evidence_path),
                    '--source-file',str(self.asset.path)]
        patcher=mock.patch.object(p,'prepare_video',return_value=self.asset)
        patcher.start();self.addCleanup(patcher.stop)

    def save(self):
        for path,doc in ((self.profile_path,self.profile),(self.evidence_path,self.evidence)):
            path.write_text(json.dumps(doc));path.chmod(0o600)

    def test_cli_profile_plan_to_container_and_get_only_confirmation(self):
        client=fixture.FakeClient(fixture.TH_ME,{'id':'700'},fixture.TH_ME,{'status':'FINISHED'},
                                 {'id':'900'},fixture.TH_ME,p.ApiError(),fixture.TH_ME,
                                 {'id':'900','media_type':'VIDEO','permalink':'https://www.threads.com/@synthetic/post/one'})
        code,plan=self.cli('plan',*self.flags,client=client)
        self.assertEqual(code,0);self.assertTrue(plan['ai_label']['value'])
        flags=[*self.flags,'--reviewed-hash',plan['content_sha256'],'--execute']
        self.assertEqual(self.cli('publish',*flags,client=client)[1]['state'],'media_processing')
        self.ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id=?",(self.profile_job,))
        self.assertEqual(self.cli('publish',*flags,client=client)[1]['state'],'confirming_label')
        before=len(client.calls)
        code,result=self.cli('publish',*flags,client=client)
        self.assertEqual((code,result['state']),(0,'published'))
        self.assertEqual(result['ai_verification'],'explicit_container_request_and_post_receipt')
        self.assertTrue(all(c[0]=='GET' for c in client.calls[before:]))
        self.assertEqual(client.calls[1][2]['is_made_with_ai'],'true')
        self.assertEqual(sum(c[1]=='/123/threads_publish' for c in client.calls),1)

    def test_instagram_crash_get_only_then_threads_failure_preserves_completed_platform(self):
        text='Approved synthetic caption'
        approval=profile.bind(self.profile,str(self.evidence_path),str(self.asset.path),'instagram',text,self.asset)
        digest=p.content_hash(text,self.asset,publication_binding=approval['binding_sha256'])
        kwargs={'publication_evidence':str(self.evidence_path),'source_file':str(self.asset.path),'reviewed_hash':digest}
        first=fixture.FakeClient(fixture.IG_ME,{'id':'700'},fixture.IG_ME,{'status_code':'FINISHED'},{'id':'900'})
        with mock.patch.dict(p.os.environ,{'MORU_PUBLICATION_PROFILE':str(self.profile_path)}):
            engine=p.Publisher('instagram','123',first,self.ledger,instagram_login_mode='instagram',**kwargs)
            self.assertEqual(engine.publish(self.profile_job,text,self.asset)['state'],'media_processing')
            self.ledger.db.execute('UPDATE jobs SET next_poll_at=0')
            with mock.patch.object(engine,'confirm_profile_receipt',side_effect=SystemExit('synthetic interruption')):
                with self.assertRaises(SystemExit):engine.publish(self.profile_job,text,self.asset)
            self.assertEqual(self.ledger.get(('instagram','123',self.profile_job))['state'],'confirming_label')
            self.ledger.close();self.ledger=p.Ledger(self.state)
            receipt={'id':'900','media_type':'VIDEO','permalink':'https://www.instagram.com/reel/synthetic/'}
            second=fixture.FakeClient(fixture.IG_ME,{**receipt,'is_ai_generated':False},fixture.IG_ME,{**receipt,'is_ai_generated':True})
            engine=p.Publisher('instagram','123',second,self.ledger,instagram_login_mode='instagram',**kwargs)
            self.assertEqual(engine.publish(self.profile_job,text,self.asset)['state'],'confirming_label')
            self.assertEqual(engine.publish(self.profile_job,text,self.asset)['state'],'published')
            self.assertTrue(all(c[0]=='GET' for c in second.calls))
            saved=self.ledger.get(('instagram','123',self.profile_job))
            threads=fixture.FakeClient(fixture.TH_ME,p.ApiError())
            target=p.Publisher('threads','123',threads,self.ledger,**kwargs)
            self.assertEqual(target.publish(self.profile_job,text,self.asset)['state'],'unknown')
            before=len(threads.calls)
            self.assertEqual(target.publish(self.profile_job,text,self.asset)['state'],'unknown')
            self.assertEqual(len(threads.calls),before)
            self.assertEqual(self.ledger.get(('instagram','123',self.profile_job)),saved)
            self.assertEqual(sum(c[1]=='/123/media_publish' for c in first.calls),1)

    def test_missing_evidence_changed_profile_or_language_blocks_without_state(self):
        client=mock.Mock()
        code,plan=self.cli('plan',*self.flags,client=client);self.assertEqual(code,0)
        code,_=self.cli('publish',*self.flags,'--execute',client=client)
        self.assertEqual(code,2)
        self.profile['max_capture_age_hours']=48;self.save()
        code,_=self.cli('publish',*self.flags,'--reviewed-hash',plan['content_sha256'],'--execute',client=client)
        self.assertEqual(code,2)
        self.evidence['ui_language']='ko';self.save()
        self.assertEqual(self.cli('plan',*self.flags,client=client)[0],2)
        self.evidence_path.unlink()
        self.assertEqual(self.cli('plan',*self.flags,client=client)[0],2)
        client.request.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    def test_platform_interval_is_independent_and_aliases_cannot_bypass_source(self):
        client=mock.Mock()
        _,plan=self.cli('plan',*self.flags,client=client)
        self.ledger.claim(('threads','123','earlier'),p.content_hash('earlier'))
        self.ledger.update(('threads','123','earlier'),'published',post_id='800')
        code,result=self.cli('publish',*self.flags,'--reviewed-hash',plan['content_sha256'],'--execute',client=client)
        self.assertEqual(code,2);self.assertIn('interval',result['error'])
        client.request.assert_not_called()
        old_job=self.profile_job;self.profile_job='renamed-capture'
        self.assertEqual(self.cli('plan',*self.flags,client=client)[0],2)
        self.profile_job=old_job
        self.profile['minimum_publish_interval_hours']=0;self.save()
        self.assertEqual(self.cli('plan',*self.flags,client=client)[0],2)

    def test_recovered_profile_path_blocks_before_any_journal_write(self):
        ledger=mock.Mock(recovered=True);ledger.get.return_value=None
        engine=p.Publisher('threads','123',mock.Mock(),ledger)
        with mock.patch.object(profile,'configured',return_value=self.profile):
            with self.assertRaisesRegex(p.SafeError,'not yet supported'):
                engine.publish(self.profile_job,'Approved synthetic caption',self.asset)
        ledger.update.assert_not_called();ledger.claim.assert_not_called()

    def test_capture_age_original_hash_and_media_disclosure_are_required(self):
        for field,value in [('fresh_capture',False),('captured_at',time.time()-90000),
                            ('source_sha256','a'*64),('subtitle_language','ko')]:
            old=self.evidence[field];self.evidence[field]=value;self.save()
            with self.assertRaises(p.SafeError):profile.bind(self.profile,str(self.evidence_path),str(self.asset.path),'threads','Approved synthetic caption',self.asset)
            self.evidence[field]=old
        self.save()
        from dataclasses import replace
        with self.assertRaises(p.SafeError):profile.bind(self.profile,str(self.evidence_path),str(self.asset.path),'threads','Approved synthetic caption',replace(self.asset,ai_generated=False))

if __name__=='__main__':unittest.main()
