import copy
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import hashlib
import io
import json
import subprocess
import unittest
from unittest import mock

import reconcile as r
import candidate_review as review
import delivery_binding as delivery
import test_candidate_review as candidate_tests


class DeliveryBindingTests(unittest.TestCase):
    write=candidate_tests.CandidateReviewTests.write
    artifact=candidate_tests.CandidateReviewTests.artifact
    register=candidate_tests.CandidateReviewTests.register

    def setUp(self):
        candidate_tests.CandidateReviewTests.setUp(self)
        self.delivery_patcher.stop()
        self.handoff_patcher.stop()
        self.url='https://scontent-example.cdninstagram.com/video.mp4?sig=private-example'
        self.video=replace(self.video,url=self.url)
        self.source_sha=hashlib.sha256(b'original-source-before-official-reencoding').hexdigest()
        self.doc['delivery'].update(source_sha256=self.source_sha,url_sha256=review.digest(self.url),
            inspection_sha256=self.artifact(b'Full comparison: official derivative depicts exact approved original; synthetic test report'))
        self.artifact(self.media.read_bytes())
        item=next(i for i in self.doc['comparisons'] if i['platform']=='instagram')
        item.update(verdict='authorized_cross_platform',paired_job_id='prior-pair',artifact_sha256=self.video.sha256)
        ledger=r.p.Ledger(self.dest)
        try:
            ledger.db.execute("UPDATE jobs SET job_id='prior-pair' WHERE platform='instagram'")
            ledger.db.execute("INSERT INTO recovery_media VALUES('instagram',?,'prior-pair')",(self.source_sha,))
        finally:ledger.close()

    @contextmanager
    def network(self,*,url=None,data=None,status=200,headers=None,actual_profile=False):
        body=self.media.read_bytes() if data is None else data
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(review,'collect',return_value=self.rows))
            stack.enter_context(mock.patch.object(delivery.runtime,'load_environment',return_value=self.config['instagram']))
            api=stack.enter_context(mock.patch.object(r.p,'APIClient'))
            api.return_value.request.return_value={'id':'777','media_type':'VIDEO','media_url':self.url if url is None else url}
            transport=stack.enter_context(mock.patch.object(delivery,'PinnedHTTPS'))
            connection=transport.return_value
            def response():
                value=mock.Mock(status=status)
                h={'Content-Type':'video/mp4','Content-Length':str(len(body)),**(headers or {})}
                value.getheader.side_effect=lambda name,default=None:h.get(name,default)
                value.read.side_effect=io.BytesIO(body).read
                return value
            connection.getresponse.side_effect=response
            profile=stack.enter_context(mock.patch.object(r.p,'prepare_video',wraps=r.p.prepare_video) if actual_profile else mock.patch.object(r.p,'prepare_video',return_value=self.video))
            yield api,connection,profile

    def prepare(self,ledger,video=None):
        ledger.prepare_review(('threads','123','one-new-job'),mock.Mock(),video=video or self.video)

    def test_official_derivative_accepted_with_distinct_source_hash_and_no_url_storage(self):
        with self.network() as (api,connection,profile):
            self.register()
            ledger=r.p.Ledger(self.dest)
            try:
                self.prepare(ledger)
                row,fresh=ledger.claim(('threads','123','one-new-job'),self.doc['candidate']['content_hash'],media_sha256=self.video.sha256)
                self.assertTrue(fresh)
                document=ledger.db.execute('SELECT document FROM recovery_reviews').fetchone()[0]
                self.assertNotIn(self.url,document)
                self.assertNotIn('private-example',document)
                self.assertNotEqual(self.source_sha,self.video.sha256)
                self.assertEqual(json.loads(document)['delivery']['remote_sha256'],self.video.sha256)
                self.assertEqual(profile.call_count,1)
                self.assertTrue(all(c.args[0]=='GET' for c in api.return_value.request.call_args_list))
                self.assertTrue(all(c.args[0]=='GET' for c in connection.request.call_args_list))
                self.assertNotIn('Authorization',connection.request.call_args.kwargs['headers'])
            finally:ledger.close()

    def test_real_ffprobe_validates_exact_local_derivative_mp4(self):
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=black:s=64x64:r=30',
                        '-t','31','-c:v','libx264','-pix_fmt','yuv420p','-an',str(self.media)],check=True,capture_output=True)
        self.media.chmod(0o600)
        self.video=r.p.prepare_video('threads',str(self.media),self.url)
        self.doc['candidate']['media_sha256']=self.video.sha256
        self.doc['candidate']['content_hash']=r.p.content_hash(self.text.read_text(),self.video)
        self.doc['delivery'].update(remote_sha256=self.video.sha256,bytes=self.video.size)
        item=next(i for i in self.doc['comparisons'] if i['platform']=='instagram')
        item['artifact_sha256']=self.artifact(self.media.read_bytes())
        with self.network(actual_profile=True) as (_,_,profile):
            self.register()
            self.assertEqual(profile.call_args.args[1],str(self.media))
            self.assertTrue(str(self.media).endswith('.mp4'))

    def test_native_publisher_hands_off_only_verified_url(self):
        with self.network():
            self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
            client.request.side_effect=[{'id':'123'},{'id':'888'}]
            try:
                result=r.p.Publisher('threads','123',client,ledger).publish('one-new-job',self.text.read_text(),self.video)
                self.assertEqual(result['state'],'media_processing')
                posts=[c for c in client.request.call_args_list if c.args[0]=='POST']
                self.assertEqual(len(posts),1)
                self.assertEqual(posts[0].args[2]['video_url'],self.url)
            finally:ledger.close()

    def test_native_publisher_changed_url_never_reaches_provider_post(self):
        with self.network():
            self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
            try:
                changed=replace(self.video,url='https://example.com/unapproved.mp4')
                with self.assertRaises(r.p.SafeError):
                    r.p.Publisher('threads','123',client,ledger).publish('one-new-job',self.text.read_text(),changed)
                self.assertFalse(any(c.args[0]=='POST' for c in client.request.call_args_list))
                self.assertIsNone(ledger.get(('threads','123','one-new-job')))
            finally:ledger.close()

    def test_expiration_between_claim_and_handoff_still_prevents_post(self):
        with self.network():
            self.register();ledger=r.p.Ledger(self.dest);client=mock.Mock()
            current=[r.time.time()]
            def identity(*args,**kwargs):
                current[0]=self.doc['delivery']['expires_at']+1
                return {'id':'123'}
            client.request.side_effect=identity
            try:
                with mock.patch.object(delivery.time,'time',side_effect=lambda:current[0]):
                    with self.assertRaises(r.p.SafeError):
                        r.p.Publisher('threads','123',client,ledger).publish('one-new-job',self.text.read_text(),self.video)
                self.assertFalse(any(c.args[0]=='POST' for c in client.request.call_args_list))
                self.assertEqual(ledger.get(('threads','123','one-new-job'))['state'],'failed')
                self.assertEqual(ledger.db.execute('SELECT state FROM recovery_reviews').fetchone()[0],'consumed')
            finally:ledger.close()

    def test_registration_checks_actual_remote_bytes_and_profile(self):
        for data,status in [(b'different-media',200),(b'',403),(b'',302),(b'',307)]:
            with self.subTest(status=status),self.network(data=data,status=status):
                with self.assertRaises(r.p.SafeError):self.register()
        with self.network() as (_,_,profile):
            profile.return_value=replace(self.video,preflight='unverified')
            with self.assertRaises(r.p.SafeError):self.register()

    def test_unrelated_https_url_or_changed_query_cannot_reuse_review(self):
        with self.network():self.register()
        for url in ['https://example.com/other.mp4',self.url+'&changed=1']:
            with self.subTest(kind='replacement'),self.network():
                ledger=r.p.Ledger(self.dest)
                try:
                    with self.assertRaises(r.p.SafeError):self.prepare(ledger,replace(self.video,url=url))
                    self.assertIsNone(ledger.get(('threads','123','one-new-job')))
                finally:ledger.close()

    def test_official_url_rotation_blocks_until_new_review_not_silent_rebinding(self):
        with self.network():self.register()
        with self.network(url=self.url+'&rotation=1'):
            ledger=r.p.Ledger(self.dest)
            try:
                with self.assertRaises(r.p.SafeError):self.prepare(ledger)
            finally:ledger.close()

    def test_runtime_expired_or_changed_remote_bytes_block_without_claim(self):
        with self.network():self.register()
        for data,status in [(b'changed',200),(b'',403),(b'',302)]:
            with self.network(data=data,status=status):
                ledger=r.p.Ledger(self.dest)
                try:
                    with self.assertRaises(r.p.SafeError):self.prepare(ledger)
                    self.assertIsNone(ledger.get(('threads','123','one-new-job')))
                finally:ledger.close()
        with self.network(),mock.patch.object(delivery.time,'time',return_value=self.doc['delivery']['expires_at']+1):
            ledger=r.p.Ledger(self.dest)
            try:
                with self.assertRaises(r.p.SafeError):self.prepare(ledger)
            finally:ledger.close()

    def test_original_file_instead_of_registered_derivative_is_rejected(self):
        with self.network():self.register()
        source=self.write('original.mp4',b'original-source-before-official-reencoding')
        original=replace(self.video,path=source,sha256=self.source_sha,size=source.stat().st_size,stamp=r.p.file_stamp(source))
        with self.network():
            ledger=r.p.Ledger(self.dest)
            try:
                with self.assertRaises(r.p.SafeError):self.prepare(ledger,original)
            finally:ledger.close()

    def test_missing_paired_source_receipt_or_inspection_evidence_blocks(self):
        for field,value in [('source_sha256','0'*64),('job_id','unrelated'),('inspection_sha256','0'*64)]:
            doc=copy.deepcopy(self.doc);doc['delivery'][field]=value
            with self.subTest(field=field),self.network():
                with self.assertRaises((r.p.SafeError,OSError)):self.register(doc)

    def test_wrong_mime_encoding_size_and_redirect_are_rejected_without_url_leak(self):
        for headers,status in [({'Content-Type':'text/html'},200),({'Content-Encoding':'gzip'},200),
                               ({'Content-Length':'1'},200),({'Location':'https://elsewhere.example/private'},301)]:
            with self.network(headers=headers,status=status):
                with self.assertRaises(r.p.SafeError) as error:delivery.remote_bytes(self.url,self.video.size,self.video.sha256)
                self.assertNotIn(self.url,str(error.exception));self.assertNotIn('private-example',str(error.exception))

    def test_dns_private_or_mixed_answers_are_blocked_and_public_ip_is_pinned(self):
        def address(ip):return (2,1,6,'',(ip,443))
        for addresses in [[address('127.0.0.1')],[address('8.8.8.8'),address('10.0.0.1')]]:
            with mock.patch.object(delivery.socket,'getaddrinfo',return_value=addresses),mock.patch.object(delivery.socket,'create_connection') as connect:
                with self.assertRaises(r.p.SafeError):delivery.PinnedHTTPS('scontent-example.cdninstagram.com').connect()
                connect.assert_not_called()
        context=mock.Mock()
        with mock.patch.object(delivery.socket,'getaddrinfo',return_value=[address('8.8.8.8')]),mock.patch.object(delivery.socket,'create_connection') as connect:
            connection=delivery.PinnedHTTPS('scontent-example.cdninstagram.com',context=context,timeout=30)
            connection.connect()
            connect.assert_called_once_with(('8.8.8.8',443),30)
            self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'],'scontent-example.cdninstagram.com')

    def test_instagram_local_registration_does_not_require_threads_delivery(self):
        self.doc['candidate'].update(platform='instagram',account_id='999')
        self.doc['delivery']=None
        item=next(i for i in self.doc['comparisons'] if i['platform']=='instagram')
        item.update(verdict='distinct',paired_job_id=None,artifact_sha256=self.artifact(b'old-instagram-rendition'))
        with mock.patch.object(delivery,'official_url') as official,mock.patch.object(delivery,'remote_bytes') as fetch:
            self.register()
            official.assert_not_called();fetch.assert_not_called()

if __name__=='__main__':unittest.main()
