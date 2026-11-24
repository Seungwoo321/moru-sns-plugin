import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import publisher as p
import video_preflight as v


class OriginalVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.good=self.root/'original.mp4';self.bad=self.root/'reencoded.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=320x240:r=24',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','31','-c:v','libx264','-pix_fmt','yuv420p',
            '-c:a','aac','-b:a','128k','-use_editlist','0','-movflags','+faststart',str(self.good)],check=True,capture_output=True)
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(self.good),'-c','copy','-use_editlist','1','-movflags','+faststart',str(self.bad)],check=True,capture_output=True)

    def media(self,path,platform='instagram',url=None):
        return p.prepare_video(platform,path,url,allow_missing_url=True)

    def test_local_pass_does_not_validate_remote_rendition_or_transfer(self):
        original=self.media(self.good,'threads')
        rendition=self.media(self.bad,'threads','https://scontent-example.cdninstagram.com/video.mp4')
        self.assertEqual(v.inspect('threads',original)['local']['reasons'],[])
        result=v.inspect('threads',rendition)
        self.assertIn('mp4_edit_list_present',result['blocking_reasons'])
        self.assertIn('https_original_approval_and_bytes_unverified',result['blocking_reasons'])
        self.assertFalse(result['delivery']['remote_copy_verified'])
        self.assertNotEqual(original.sha256,rendition.sha256)
        client=mock.Mock();ledger=mock.Mock();ledger.get.return_value=None;ledger.recovered=False
        engine=p.Publisher('threads','123',client,ledger)
        with mock.patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(p.SafeError,'mp4_edit_list_present'):
            engine.publish('new-video','New approved capture',rendition)
        client.request.assert_not_called();ledger.claim.assert_not_called()

    def test_unified_original_passes_gate_but_bad_container_does_not(self):
        v.require_new('instagram',self.media(self.good),'resumable')
        good=v.inspect('instagram',self.media(self.good),'resumable')
        self.assertEqual(good['delivery']['status'],'local_original_upload_available')
        self.assertFalse(good['publish_ready']);self.assertFalse(good['delivery']['bytes_sent'])
        with self.assertRaisesRegex(p.SafeError,'mp4_edit_list_present'):
            v.require_new('instagram',self.media(self.bad),'resumable')
        with self.assertRaisesRegex(p.SafeError,'approval missing'):
            v.require_new('threads',self.media(self.good,'threads','https://example.com/original.mp4'))

    def test_plan_reports_only_requested_platform_and_exact_hold_reason(self):
        text=self.root/'caption.txt';text.write_text('Approved game capture')
        output=io.StringIO()
        with mock.patch.dict(os.environ,{},clear=True),contextlib.redirect_stdout(output):
            code=p.main(['plan','--platform','threads','--state-dir',str(self.root/'unused'),
                         '--job-id','new-video','--text-file',str(text),'--video-file',str(self.good)])
        self.assertEqual(code,0);result=json.loads(output.getvalue())
        self.assertEqual(result['platform_video_preflight']['platform'],'threads')
        self.assertIn('official_original_transfer_path_missing',result['readiness']['reasons'])
        self.assertEqual(result['platform_video_preflight']['delivery']['message'],'공식 원본 전송 경로 없음')
        self.assertEqual(result['network_requests'],0);self.assertFalse((self.root/'unused').exists())

    def approve_url(self,platform):
        import time,hashlib
        media=self.media(self.good,platform,'https://media.example.com/approved-original.mp4')
        approval={'version':1,'platform':platform,'account_id':'123','job_id':'approved-original',
                  'media_sha256':media.sha256,'url_sha256':hashlib.sha256(media.url.encode()).hexdigest(),
                  'content_sha256':p.content_hash('Approved capture',media),'approval_record':'synthetic-account-approval',
                  'final_original':True,'expires_at':time.time()+600}
        path=self.root/'approval.json';path.write_text(json.dumps(approval));path.chmod(0o600)
        return media,path,approval

    def checked_response(self,path):
        data=path.read_bytes();response=mock.Mock();response.status=200
        response.getheader.side_effect=lambda k,default=None: {'Content-Type':'video/mp4','Content-Length':str(len(data)),'Content-Encoding':'identity'}.get(k,default)
        response.read.side_effect=io.BytesIO(data).read
        return response

    def test_approved_https_originals_for_both_platforms_read_only(self):
        import delivery_binding as delivery
        for platform in ('threads','instagram'):
            media,path,approval=self.approve_url(platform)
            before=self.good.read_bytes();self.good.chmod(0o400)
            # Re-prepare after permission change so the original immutable stamp is exact.
            media=self.media(self.good,platform,media.url)
            with mock.patch.dict(os.environ,{'MORU_VIDEO_URL_APPROVAL':str(path)},clear=True),mock.patch.object(delivery,'PinnedHTTPS') as connection:
                connection.return_value.getresponse.return_value=self.checked_response(self.good)
                v.require_new(platform,media,account_id='123',job_id='approved-original',text='Approved capture')
                self.assertEqual(connection.return_value.request.call_args.args[0],'GET')
                self.assertNotIn('Authorization',connection.return_value.request.call_args.kwargs['headers'])
            self.assertEqual(before,self.good.read_bytes());self.assertEqual(self.good.stat().st_mode & 0o777,0o400)

    def test_approved_local_pass_remote_bytes_mismatch_before_container(self):
        import delivery_binding as delivery
        media,path,approval=self.approve_url('threads')
        client=mock.Mock();ledger=mock.Mock();ledger.get.return_value=None;ledger.recovered=False
        engine=p.Publisher('threads','123',client,ledger)
        with mock.patch.dict(os.environ,{'MORU_VIDEO_URL_APPROVAL':str(path)},clear=True),mock.patch.object(delivery,'PinnedHTTPS') as connection:
            connection.return_value.getresponse.return_value=self.checked_response(self.bad)
            with self.assertRaisesRegex(p.SafeError,'fetch failed or bytes changed'):
                engine.publish('approved-original','Approved capture',media)
        client.request.assert_not_called();ledger.claim.assert_not_called()

    def test_native_cli_preserves_both_approved_https_container_paths(self):
        import delivery_binding as delivery
        import runtime_guard
        import test_publisher as fixtures
        caption=self.root/'caption.txt';caption.write_text('Approved capture')
        for platform in ('threads','instagram'):
            media,approval_path,approval=self.approve_url(platform)
            state=self.root/('state-'+platform)
            runtime_guard.initialize_state(state,'synthetic-url-runtime',{
                platform:{'account_id':'123','username':'test_bot','auth_mode':'instagram' if platform=='instagram' else None}})
            client=fixtures.FakeClient(fixtures.TH_ME if platform=='threads' else fixtures.IG_ME,{'id':'700'})
            args=['publish','--platform',platform,'--state-dir',str(state),'--job-id','approved-original',
                  '--text-file',str(caption),'--video-file',str(self.good),'--video-url',media.url,'--execute']
            if platform=='instagram':args+=['--instagram-login-mode','instagram']
            output=io.StringIO()
            env={'MORU_VIDEO_URL_APPROVAL':str(approval_path),platform.upper()+'_EXPECTED_USER_ID':'123',
                 platform.upper()+'_USER_ACCESS_TOKEN':'synthetic-only'}
            with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(delivery,'PinnedHTTPS') as connection,mock.patch.object(p,'APIClient',type('APIStub',(),{'__new__':lambda cls,*a,**kw:client})),contextlib.redirect_stdout(output):
                connection.return_value.getresponse.return_value=self.checked_response(self.good)
                code=p.main(args)
            self.assertEqual(code,3,output.getvalue());self.assertEqual(json.loads(output.getvalue())['state'],'media_processing')
            posts=[c for c in client.calls if c[0]=='POST']
            self.assertEqual(len(posts),1);self.assertEqual(posts[0][2]['video_url'],media.url)
            self.assertEqual(posts[0][2]['media_type'],'VIDEO' if platform=='threads' else 'REELS')
            self.assertFalse(any('publish' in c[1] for c in client.calls))

    def test_wrong_account_url_or_content_never_fetches(self):
        import delivery_binding as delivery
        media,path,approval=self.approve_url('threads')
        with mock.patch.dict(os.environ,{'MORU_VIDEO_URL_APPROVAL':str(path)},clear=True),mock.patch.object(delivery,'remote_bytes') as fetch:
            for account,job,text in [('999','approved-original','Approved capture'),('123','other','Approved capture'),('123','approved-original','Changed caption')]:
                with self.assertRaisesRegex(p.SafeError,'approval missing'):
                    v.require_new('threads',media,account_id=account,job_id=job,text=text)
            from dataclasses import replace
            with self.assertRaisesRegex(p.SafeError,'approval missing'):
                v.require_new('threads',replace(media,url='https://elsewhere.example.com/different.mp4'),account_id='123',job_id='approved-original',text='Approved capture')
            fetch.assert_not_called()

    def test_approval_is_rechecked_at_container_handoff(self):
        import time,hashlib
        media,path,approval=self.approve_url('threads')
        proof=('threads','123','approved-original',p.content_hash('Approved capture',media),media.sha256,
               hashlib.sha256(media.url.encode()).hexdigest(),time.time()+600)
        v.check_handoff(proof,'threads','123','approved-original','Approved capture',media)
        for candidate in (proof[:-1]+(time.time()-1,),('instagram',)+proof[1:]):
            with self.assertRaisesRegex(p.SafeError,'expired or changed'):
                v.check_handoff(candidate,'threads','123','approved-original','Approved capture',media)

    def test_late_moov_and_symlink_are_read_only_rejections(self):
        late=self.root/'late.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(self.good),'-c','copy','-use_editlist','0',str(late)],check=True,capture_output=True)
        late.chmod(0o400);before=late.read_bytes()
        result=v.inspect('instagram',self.media(late),'resumable')
        self.assertIn('mp4_moov_after_media',result['blocking_reasons'])
        self.assertNotIn('mp4_edit_list_present',result['blocking_reasons'])
        with self.assertRaisesRegex(p.SafeError,'mp4_moov_after_media'):v.require_new('instagram',self.media(late),'resumable')
        self.assertEqual(before,late.read_bytes())
        link=self.root/'link.mp4';link.symlink_to(self.good)
        with self.assertRaises(OSError):v.container_checks(link)

    def test_audio_limits_still_fail_before_container(self):
        probe={'format':{'format_name':'mp4','duration':'31'},'streams':[
            {'codec_type':'video','codec_name':'h264','pix_fmt':'yuv420p','width':320,'avg_frame_rate':'24/1','field_order':'progressive'},
            {'codec_type':'audio','codec_name':'aac','sample_rate':'96000','channels':2}]}
        with mock.patch.object(p.subprocess,'run',return_value=mock.Mock(stdout=json.dumps(probe).encode())):
            with self.assertRaisesRegex(p.SafeError,'ffprobe profile'):self.media(self.good,'threads')

if __name__=='__main__':unittest.main()
