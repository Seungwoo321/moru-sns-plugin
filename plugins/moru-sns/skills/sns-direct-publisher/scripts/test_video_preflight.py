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
        self.assertIn('official_original_transfer_path_missing',result['blocking_reasons'])
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
        with self.assertRaisesRegex(p.SafeError,'official_original_transfer_path_missing'):
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

    def test_audio_limits_still_fail_before_container(self):
        probe={'format':{'format_name':'mp4','duration':'31'},'streams':[
            {'codec_type':'video','codec_name':'h264','pix_fmt':'yuv420p','width':320,'avg_frame_rate':'24/1','field_order':'progressive'},
            {'codec_type':'audio','codec_name':'aac','sample_rate':'96000','channels':2}]}
        with mock.patch.object(p.subprocess,'run',return_value=mock.Mock(stdout=json.dumps(probe).encode())):
            with self.assertRaisesRegex(p.SafeError,'ffprobe profile'):self.media(self.good,'threads')

if __name__=='__main__':unittest.main()
