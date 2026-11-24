import copy
from contextlib import ExitStack,contextmanager
import io
import json
import os
import subprocess
import unittest
from unittest import mock

import reconcile as r
import candidate_review as review
import delivery_binding as delivery
import test_candidate_pair as base


class PairImageTests(unittest.TestCase):
    write=base.CandidatePairTests.write
    artifact=base.CandidatePairTests.artifact
    register_pair=base.CandidatePairTests.register_pair
    video=base.CandidatePairTests.video
    review_doc=base.CandidatePairTests.review_doc
    approve=base.CandidatePairTests.approve
    publisher=base.CandidatePairTests.publisher
    finish=base.CandidatePairTests.finish

    def setUp(self):
        base.CandidatePairTests.setUp(self)
        self.register_pair();self.approve('instagram');self.finish('instagram')
        for patch in self.delivery_patches:patch.stop()
        self.image_path=self.root/'official-thumbnail.jpg'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=640x1136','-frames:v','1',str(self.image_path)],capture_output=True,check=True)
        self.image_path.chmod(0o600)
        self.url='https://scontent-example.cdninstagram.com/thumbnail.jpg?sig=private-test'
        self.image=r.p.prepare_image(str(self.image_path),self.url)
        self.still_text=self.write('still.txt',b'A still frame from the game workspace.')
        self.doc=self.review_doc('threads');self.doc['version']=3
        self.doc['fallback_approval_record']='user-approved-still-caption'
        self.doc['fallback_report_sha256']=self.artifact(b'Private report binding exact JPEG and still caption to own confirmed Reel')
        self.doc['candidate'].update(media_sha256=self.image.sha256,text_sha256=review.file_digest(self.still_text),content_hash=r.p.content_hash(self.still_text.read_text(),self.image))
        self.doc['delivery'].update(kind='IMAGE',width=640,height=1136,bytes=self.image.size,remote_sha256=self.image.sha256,
                                   url_sha256=review.digest(self.url),inspection_sha256=self.doc['fallback_report_sha256'])
        self.artifact(self.image_path.read_bytes())
        item=next(i for i in self.doc['comparisons'] if i['post_id']=='301')
        item.update(source_kind='official_public_thumbnail',artifact_sha256=self.image.sha256)

    @contextmanager
    def network(self,url=None,body=None,status=200):
        data=self.image_path.read_bytes() if body is None else body
        proxy='http://approved-proxy:8080'
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ,{'HTTPS_PROXY':proxy,'https_proxy':proxy,'MORU_CDN_PROXY_SHA256':review.digest(proxy),'MORU_CDN_PROXY_HOST':'scontent-example.cdninstagram.com','NO_PROXY':'','no_proxy':''}))
            stack.enter_context(mock.patch.object(delivery.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]))
            stack.enter_context(mock.patch.object(review,'collect',return_value=self.rows))
            stack.enter_context(mock.patch.object(delivery.runtime,'load_environment',return_value=self.config['instagram']))
            api=stack.enter_context(mock.patch.object(r.p,'APIClient'))
            api.return_value.request.return_value={'id':'301','media_type':'VIDEO','thumbnail_url':url or self.url}
            direct=stack.enter_context(mock.patch.object(delivery,'PinnedHTTPS',side_effect=AssertionError('No direct fallback')))
            opener=stack.enter_context(mock.patch.object(delivery.urllib.request,'build_opener'))
            def response(*a,**kw):
                result=mock.Mock(status=status)
                headers={'Content-Type':'image/jpeg','Content-Length':str(len(data))}
                result.getheader.side_effect=lambda n,default=None:headers.get(n,default)
                result.read.side_effect=io.BytesIO(data).read
                return result
            opener.return_value.open.side_effect=response
            yield api,opener,direct

    def approve_image(self,doc=None):
        return review.register(self.dest,doc or self.doc,self.image_path,self.still_text,self.artifacts,
                               collector=lambda _:self.rows,pair_id='one-approved-pair',image_mode=True)

    def test_exact_image_e2e_via_proxy_with_same_pair_and_changed_still_caption(self):
        ledger=r.p.Ledger(self.dest)
        try:old=tuple(ledger.db.execute('SELECT document,digest FROM candidate_pair').fetchone())
        finally:ledger.close()
        with self.network() as (api,opener,direct):
            self.approve_image();ledger=r.p.Ledger(self.dest);client=mock.Mock()
            def request(method,path,*a,**kw):
                if path=='/me':return {'id':'123'}
                if path=='/123/threads':return {'id':'901'}
                if path=='/901':return {'status':'FINISHED'}
                if path=='/123/threads_publish':return {'id':'401'}
                if path=='/401':return {'id':'401','permalink':'https://www.threads.com/@synthetic/post/still','media_type':'IMAGE'}
                raise AssertionError(path)
            client.request.side_effect=request
            try:
                engine=self.publisher('threads',ledger,client)
                self.assertEqual(engine.publish('pair-threads',self.still_text.read_text(),self.image)['state'],'media_processing')
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='pair-threads'")
                result=engine.publish('pair-threads',self.still_text.read_text(),self.image)
                self.assertEqual((result['state'],result['media_type']),('published','IMAGE'))
                creation=next(c for c in client.request.call_args_list if c.args[1]=='/123/threads')
                self.assertEqual(creation.args[2]['media_type'],'IMAGE');self.assertEqual(creation.args[2]['image_url'],self.url)
                self.assertNotIn('video_url',creation.args[2])
                self.assertEqual(tuple(ledger.db.execute('SELECT document,digest FROM candidate_pair').fetchone()),old)
                self.assertEqual(ledger.get(('threads','123','legacy-unknown'))['state'],'unknown')
                self.assertFalse(json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])['inventory_complete'])
                self.assertTrue(all('thumbnail_url' in c.kwargs['params']['fields'] for c in api.return_value.request.call_args_list))
                self.assertTrue(all(c.args[0].get_method()=='GET' for c in opener.return_value.open.call_args_list))
                direct.assert_not_called()
            finally:ledger.close()

    def test_wrong_url_bytes_source_and_history_block_without_review(self):
        for kw in ({'url':self.url+'&changed=1'},{'body':b'wrong'},{'status':403}):
            with self.network(**kw),self.assertRaises(r.p.SafeError):self.approve_image()
        doc=copy.deepcopy(self.doc);doc['delivery']['post_id']='unrelated'
        with self.network(),self.assertRaises(r.p.SafeError):self.approve_image(doc)
        self.rows[0]['published_at']+=1
        with self.network(),self.assertRaises(r.p.SafeError):self.approve_image()

    def test_existing_threads_attempt_blocks_fallback_without_deletion(self):
        ledger=r.p.Ledger(self.dest)
        try:ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state) VALUES('threads','123','pair-threads','unknown')")
        finally:ledger.close()
        with self.network(),self.assertRaises(r.p.SafeError):self.approve_image()

    def test_caption_changes_after_registration_or_second_approval_are_blocked(self):
        with self.network():
            self.approve_image()
            with self.assertRaises(r.p.SafeError):self.approve_image()
            ledger=r.p.Ledger(self.dest)
            try:
                client=mock.Mock();engine=self.publisher('threads',ledger,client)
                with self.assertRaises(r.p.SafeError):engine.publish('pair-threads','A different still caption',self.image)
                self.assertFalse(any(c.args[0]=='POST' for c in client.request.call_args_list))
            finally:ledger.close()

    def test_wrong_final_media_type_is_quarantined_with_post_id_retained(self):
        with self.network():
            self.approve_image();ledger=r.p.Ledger(self.dest);client=mock.Mock()
            client.request.side_effect=[{'id':'123'},{'id':'901'},{'id':'123'},{'status':'FINISHED'},
                                        {'id':'401'},{'id':'401','permalink':'https://www.threads.com/@synthetic/post/wrong','media_type':'VIDEO'}]
            try:
                engine=self.publisher('threads',ledger,client)
                engine.publish('pair-threads',self.still_text.read_text(),self.image)
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='pair-threads'")
                result=engine.publish('pair-threads',self.still_text.read_text(),self.image)
                self.assertEqual((result['state'],result['post_id']),('unknown','401'))
                with self.assertRaises(r.p.SafeError):engine.publish('pair-threads',self.still_text.read_text(),self.image)
            finally:ledger.close()

    def test_uncertain_image_publish_never_retries(self):
        with self.network():
            self.approve_image();ledger=r.p.Ledger(self.dest);client=mock.Mock()
            client.request.side_effect=[{'id':'123'},{'id':'901'},{'id':'123'},{'status':'FINISHED'},r.p.ApiError()]
            try:
                engine=self.publisher('threads',ledger,client)
                engine.publish('pair-threads',self.still_text.read_text(),self.image)
                ledger.db.execute("UPDATE jobs SET next_poll_at=0 WHERE job_id='pair-threads'")
                self.assertEqual(engine.publish('pair-threads',self.still_text.read_text(),self.image)['state'],'unknown')
                with self.assertRaises(r.p.SafeError):engine.publish('pair-threads',self.still_text.read_text(),self.image)
                self.assertEqual(sum(c.args[1]=='/123/threads_publish' for c in client.request.call_args_list),1)
            finally:ledger.close()

if __name__=='__main__':unittest.main()
