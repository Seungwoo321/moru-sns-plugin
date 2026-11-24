import contextlib
import io
import json
from unittest import mock
import unittest

import publisher as p
import media_policy
import test_publisher as fixture


class MediaPolicyTests(unittest.TestCase):
    setUp = fixture.PublisherTests.setUp
    tearDown = fixture.PublisherTests.tearDown
    video = fixture.PublisherTests.video
    make = fixture.PublisherTests.make

    def cli(self, command, *flags, client=None):
        text = self.state.parent / 'caption.txt'
        text.write_text('Approved synthetic caption')
        args = [command, '--platform', 'threads', '--state-dir', str(self.state),
                '--job-id', getattr(self,'profile_job','source-one'), '--text-file', str(text), *flags]
        output = io.StringIO()
        original_init = p.Publisher.__init__
        with mock.patch.dict(p.os.environ, {'THREADS_EXPECTED_USER_ID':'123',
                'THREADS_USER_ACCESS_TOKEN':'synthetic-token', **({'MORU_PUBLICATION_PROFILE':str(self.profile_path)} if hasattr(self,'profile_path') else {})}, clear=True), \
                mock.patch.object(p,'APIClient',return_value=client), \
                mock.patch.object(p.Publisher,'__init__',autospec=True,side_effect=lambda obj,*a,**kw: original_init(obj,*a,**kw,wait=lambda _:None)), contextlib.redirect_stdout(output):
            code = p.main(args)
        return code,json.loads(output.getvalue())

    def test_video_has_priority_over_screenshot_and_image_has_no_ig_target(self):
        self.assertEqual(media_policy.formats(approved_video=True,approved_screenshot=True),
                         {'threads':'VIDEO','instagram':'REELS'})
        self.assertEqual(media_policy.formats(approved_screenshot=True),{'threads':'IMAGE'})

    def test_actual_cli_blocks_text_downgrade_without_writes_or_requests(self):
        before = (self.state/'publisher.sqlite3').read_bytes()
        client = mock.Mock()
        for kind in ('video','screenshot'):
            code,result=self.cli('publish','--approved-source-kind',kind,'--execute',client=client)
            self.assertEqual(code,2);self.assertIn('format must be preserved',result['error'])
        client.request.assert_not_called()
        self.assertEqual(before,(self.state/'publisher.sqlite3').read_bytes())

    def test_video_preflight_failure_does_not_create_text_container(self):
        asset=self.video('threads')
        client=mock.Mock()
        with mock.patch.object(p,'prepare_video',side_effect=p.SafeError('Video profile failed')):
            code,_=self.cli('publish','--approved-source-kind','video','--video-file',str(asset.path),
                            '--video-url',asset.url,'--execute',client=client)
        self.assertEqual(code,2);client.request.assert_not_called()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    def test_cli_plan_disclosure_matches_actual_container_and_cannot_change_on_retry(self):
        client=fixture.FakeClient(fixture.TH_ME,{'id':'700'},{'status':'FINISHED'},
                                 {'id':'900'},fixture.TH_ME,{'id':'900','media_type':'TEXT_POST','permalink':'https://www.threads.com/@synthetic/post/one'})
        flags=('--approved-source-kind','text','--ai-generated-text')
        code,plan=self.cli('plan',*flags,client=client)
        self.assertEqual(code,0);self.assertEqual(plan['ai_label']['value'],True)
        client.calls.clear()
        code,result=self.cli('publish',*flags,'--execute',client=client)
        self.assertEqual((code,result['state']),(0,'published'))
        create=next(c for c in client.calls if c[1]=='/123/threads')
        self.assertEqual(create[2]['is_made_with_ai'],'true')
        self.assertEqual(result['content_hash'],plan['content_sha256'])
        count=len(client.calls)
        code,_=self.cli('publish','--approved-source-kind','text','--execute',client=client)
        self.assertEqual(code,2);self.assertEqual(len(client.calls),count)
        self.assertEqual(self.ledger.get(('threads','123','source-one'))['state'],'published')

    def test_ordinary_capture_and_coding_do_not_imply_ai_label(self):
        video=self.video('threads')
        pub,client=self.make('threads',fixture.TH_ME,{'id':'700'})
        self.assertEqual(pub.publish('ordinary','Game made with AI coding tools',video)['state'],'media_processing')
        self.assertEqual(client.calls[1][2]['is_made_with_ai'],'false')

    def test_explicit_ai_video_and_changed_disclosure_bind_hash(self):
        video=self.video('threads',ai=True)
        pub,client=self.make('threads',fixture.TH_ME,{'id':'700'})
        pub.publish('ai','Approved generated scene',video)
        self.assertEqual(client.calls[1][2]['is_made_with_ai'],'true')
        from dataclasses import replace
        with self.assertRaises(p.SafeError):pub.publish('ai','Approved generated scene',replace(video,ai_generated=False))
        self.assertEqual(len(client.calls),2)

    def test_instagram_label_is_get_verified_and_uncertainty_never_reposts(self):
        video=self.video('instagram',ai=True)
        pub,client=self.make('instagram',fixture.IG_ME,{'id':'700'},fixture.IG_ME,
                            {'status_code':'FINISHED'},{'id':'900'},fixture.IG_ME,
                            {'id':'900','media_type':'VIDEO','permalink':'https://www.instagram.com/reel/test/'},
                            fixture.IG_ME,{'id':'900','media_type':'VIDEO','permalink':'https://www.instagram.com/reel/test/','is_ai_generated':True})
        self.assertEqual(pub.publish('label','Approved generated scene',video)['state'],'media_processing')
        self.now+=60
        self.assertEqual(pub.publish('label','Approved generated scene',video)['state'],'confirming_label')
        with self.assertRaises(p.SafeError):pub.metrics('label')
        self.assertEqual(pub.publish('label','Approved generated scene',video)['state'],'published')
        self.assertEqual(sum(c[1]=='/123/media_publish' for c in client.calls),1)
        self.assertEqual(client.calls[-1][3]['fields'],'id,permalink,media_type,is_ai_generated')

    def test_legacy_unconfigured_text_plan_keeps_v016_disclosure(self):
        code,plan=self.cli('plan',client=mock.Mock())
        self.assertEqual(code,0);self.assertTrue(plan['ai_label']['value'])
        self.assertEqual(plan['content_sha256'],p.content_hash('Approved synthetic caption'))

    def test_unified_reel_confirmation_requires_media_product_type(self):
        video=self.video('instagram',ai=True)
        key=('instagram','123','unified-label')
        self.ledger.claim(key,p.content_hash('scene',video),auth_mode='unified')
        self.ledger.update(key,'confirming_label',post_id='900')
        receipt={'id':'900','media_type':'VIDEO','is_ai_generated':True,'permalink':'https://www.instagram.com/reel/test/'}
        client=fixture.FakeClient({'data':[{'id':'123'}]},receipt,{'data':[{'id':'123'}]},{**receipt,'media_product_type':'REELS'})
        pub=p.Publisher('instagram','123',client,self.ledger,instagram_login_mode='unified')
        self.assertEqual(pub.publish('unified-label','scene',video)['state'],'confirming_label')
        self.assertEqual(pub.publish('unified-label','scene',video)['state'],'published')
        self.assertTrue(all(c[0]=='GET' for c in client.calls))
        self.assertIn('media_product_type',client.calls[-1][3]['fields'])

    def test_failed_or_unknown_work_cannot_restart_after_policy_change(self):
        for state in ('failed','unknown'):
            key=('threads','123',state)
            self.ledger.claim(key,p.content_hash('same text'))
            self.ledger.update(key,state)
            pub,client=self.make('threads')
            self.assertEqual(pub.publish(state,'same text')['state'],state)
            self.assertEqual(client.calls,[])

if __name__=='__main__':unittest.main()
