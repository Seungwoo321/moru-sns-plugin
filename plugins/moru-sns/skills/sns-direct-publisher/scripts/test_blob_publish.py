import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import publisher as p
import runtime_guard
import blob_publish as b


class NativeBlobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.root.chmod(0o700)
        self.state=self.root/'state';self.receipts=self.root/'receipts';self.receipts.mkdir(mode=0o700)
        runtime_guard.initialize_state(self.state,'synthetic-native-blob',{'threads':{'account_id':'123','username':'synthetic','auth_mode':None}})
        self.video=self.root/'final.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=320x240:r=24','-t','31',
                        '-c:v','libx264','-pix_fmt','yuv420p','-an','-use_editlist','0','-movflags','+faststart',str(self.video)],check=True,capture_output=True)
        self.video.chmod(0o400)
        self.media=p.prepare_video('threads',self.video,ai_generated=True,allow_missing_url=True)
        self.job='capture-'+self.media.sha256
        self.text=self.write('text.txt',b'Approved synthetic capture')
        self.host='synthetic.private.blob.vercel-storage.com'
        self.config=self.write('config.json',json.dumps({'version':1,'enabled':True,'store_host':self.host,
            'receipt_dir':str(self.receipts),'approved_slack_channels':['DSYNTHETIC']}).encode())
        self.approval=self.write('approval.json',json.dumps({'version':1,'platform':'threads','account_id':'123','job_id':self.job,
            'media_sha256':self.media.sha256,'content_sha256':p.content_hash(self.text.read_text(),self.media),
            'slack_channel':'DSYNTHETIC','slack_file_id':'FSYNTHETIC','slack_message_ts':'1.000001',
            'approval_record':'synthetic-approval','final_original':True,'ai_generated':True}).encode())
        self.env={'MORU_PRIVATE_BLOB_CONFIG':str(self.config),'MORU_BLOB_READ_WRITE_TOKEN':'synthetic-blob-secret',
                  'THREADS_EXPECTED_USER_ID':'123','THREADS_USER_ACCESS_TOKEN':'synthetic-meta-secret'}
        self.sdk=[];self.api=[];self.delete_fail=False;self.put_fail=False;self.post_fail=False;self.fetch_fail=False
        outer=self
        class Provider:
            def __init__(self,*a,**kw):pass
            def request(self,method,path,payload=None,params=None):
                outer.api.append((method,path,payload))
                if path=='/me':return {'id':'123','username':'synthetic'}
                if path=='/123/threads' and method=='GET':return {'data':[]}
                if path=='/123/threads':return {'id':'700'}
                if path=='/700':return {'status':'FINISHED'}
                if path=='/123/threads_publish':
                    if outer.post_fail:raise p.ApiError()
                    return {'id':'900'}
                if path=='/900':return {'id':'900','media_type':'VIDEO','permalink':'https://www.threads.com/@synthetic/post/one'}
                raise AssertionError(path)
        self.Provider=Provider

    def write(self,name,data):
        path=self.root/name;path.write_bytes(data);path.chmod(0o600);return path

    def sdk_call(self,action,body,fd=None):
        self.sdk.append(action)
        base='https://'+self.host+'/'+body['pathname']
        if action=='check':return {'supported':True}
        if action=='put':
            self.assertIsNotNone(fd)
            self.assertEqual(hashlib.sha256(os.pread(fd,self.media.size,0)).hexdigest(),self.media.sha256)
            if self.put_fail:raise TimeoutError('synthetic private provider error')
            return {'url':base,'pathname':body['pathname'],'contentType':'video/mp4','etag':'synthetic-etag'}
        if action=='sign':return {'url':base+'?vercel-blob-valid-until='+str(body['validUntil'])+'&signature=PRIVATE_SYNTHETIC','operation':'get','validUntil':body['validUntil']}
        if action=='absent':return {'absent':True,'complete':True,'prefix':body['pathname'],'pages':1}
        if action=='head':return {'url':base,'pathname':body['pathname'],'size':self.media.size,'contentType':'video/mp4','etag':'synthetic-etag'}
        if action=='verify_delete':return {'deleted':True}
        if action=='delete':
            self.assertEqual(body['etag'],'synthetic-etag')
            if self.delete_fail:raise TimeoutError('synthetic delete uncertain')
            return {'deleted':True}
        raise AssertionError(action)

    def fetch(self,url,size,sha,**kwargs):
        self.assertEqual(sha,self.media.sha256);self.assertEqual(size,self.media.size)
        self.assertEqual(kwargs['approved_host'],self.host)
        if self.fetch_fail:raise p.SafeError('Synthetic exact byte mismatch')

    def invoke(self,command='publish',enabled=True):
        output=io.StringIO();env=self.env if enabled else {}
        args=[command,'--state-dir',str(self.state),'--approval',str(self.approval),'--video-file',str(self.video),'--text-file',str(self.text)]
        args+=getattr(self,'profile_flags',[])
        if command in ('publish','recover-absent'):args+=['--execute']
        with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(b,'sdk_call',side_effect=self.sdk_call),mock.patch.object(b.delivery_binding,'remote_bytes',side_effect=self.fetch),mock.patch.object(p,'APIClient',self.Provider),contextlib.redirect_stdout(output):
            code=b.main(args)
        value=output.getvalue()
        self.assertNotIn('PRIVATE_SYNTHETIC',value);self.assertNotIn('synthetic-blob-secret',value)
        for file in self.receipts.glob('*.json'):self.assertNotIn('PRIVATE_SYNTHETIC',file.read_text())
        return code,json.loads(value)

    def due(self):
        ledger=p.Ledger(self.state);ledger.db.execute('UPDATE jobs SET next_poll_at=0 WHERE job_id=?',(self.job,));ledger.close()

    def test_native_entrypoint_upload_publish_confirm_delete_and_no_repeat(self):
        before=self.video.read_bytes()
        self.assertEqual(self.invoke()[1]['status'],'media_processing');self.due()
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.sdk.count('delete'),1)
        self.assertEqual(sum(c[1]=='/123/threads_publish' for c in self.api),1)
        count=len(self.api);self.assertEqual(self.invoke()[1]['status'],'cleaned');self.assertEqual(len(self.api),count)
        self.assertEqual(before,self.video.read_bytes());self.assertEqual(self.video.stat().st_mode&0o777,0o400)

    def test_cleanup_retry_after_restart_never_posts(self):
        self.invoke();self.due();self.delete_fail=True
        self.assertEqual(self.invoke()[1]['status'],'cleanup_pending')
        previous=len(self.api);self.delete_fail=False
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertTrue(all(c[0]=='GET' for c in self.api[previous:]))
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.sdk.count('delete'),2)

    def test_unknown_publish_preserves_copy_and_only_reads_history(self):
        self.invoke();self.due();self.post_fail=True
        self.assertEqual(self.invoke()[1]['status'],'unknown')
        count=len(self.api)
        self.assertEqual(self.invoke()[1]['status'],'publication_unknown')
        self.assertTrue(all(c[0]=='GET' for c in self.api[count:]));self.assertNotIn('delete',self.sdk)
        self.assertEqual(self.sdk.count('put'),1)

    def test_unknown_upload_and_byte_failure_block_retries(self):
        self.put_fail=True;self.assertEqual(self.invoke()[0],2);calls=len(self.sdk)
        self.assertEqual(self.invoke()[0],2);self.assertEqual(len(self.sdk),calls);self.assertEqual(self.api,[])

    def test_lost_put_response_recovery_never_reputs_then_deletes_exact_object(self):
        self.put_fail=True;self.assertEqual(self.invoke()[0],2)
        self.put_fail=False
        self.assertEqual(self.invoke('recover')[1]['status'],'object_recovered')
        self.assertEqual(self.sdk.count('head'),2);self.assertEqual(self.api,[])
        self.assertEqual(self.invoke()[1]['status'],'media_processing');self.due()
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.sdk.count('delete'),1)

    def test_process_exit_after_put_before_receipt_can_recover(self):
        original=b.save
        def crash(path,record):
            if record['state']=='uploaded':raise SystemExit('synthetic crash after successful PUT')
            return original(path,record)
        with mock.patch.object(b,'save',side_effect=crash),self.assertRaises(SystemExit):self.invoke()
        receipt=next(self.receipts.glob('*.json'))
        self.assertEqual(json.loads(receipt.read_text())['state'],'upload_started')
        self.assertEqual(self.invoke('recover')[1]['status'],'object_recovered')
        self.assertEqual(self.invoke()[1]['status'],'media_processing');self.due()
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.sdk.count('delete'),1)

    def test_interrupted_verification_can_recover_read_only(self):
        self.fetch_fail=True;self.assertEqual(self.invoke()[0],2)
        self.assertEqual(self.invoke('recover')[0],2);self.assertEqual(self.api,[])
        self.fetch_fail=False
        self.assertEqual(self.invoke('recover')[1]['status'],'object_recovered')
        self.assertEqual(self.sdk.count('put'),1);self.assertEqual(self.api,[])

    def test_recovery_rejects_missing_changed_or_wrong_object_without_writes(self):
        self.put_fail=True;self.invoke();original=self.sdk_call
        for bad in ('missing','etag','size','contentType','pathname'):
            calls=[0]
            def changed(action,body,fd=None):
                result=original(action,body,fd)
                if action=='head':
                    calls[0]+=1
                    if bad=='missing':raise TimeoutError()
                    if bad=='etag':result['etag']='second-generation' if calls[0]>1 else 'first-generation'
                    elif bad=='size':result['size']+=1
                    elif bad=='contentType':result['contentType']='text/plain'
                    elif bad=='pathname':result['pathname']='other'
                return result
            self.sdk_call=changed
            self.assertEqual(self.invoke('recover')[0],2)
        self.assertEqual(self.sdk.count('put'),1);self.assertNotIn('delete',self.sdk);self.assertEqual(self.api,[])

    def test_explicit_absence_preserves_old_receipt_and_continues_same_path_once(self):
        self.put_fail=True;self.invoke();self.put_fail=False
        receipt=next(self.receipts.glob('*.json'));original=receipt.read_bytes();old=json.loads(original)
        count=len(self.api);puts=self.sdk.count('put')
        self.assertEqual(self.invoke('recover-absent')[1]['status'],'absent_continuation_prepared')
        self.assertEqual(self.api[count:],[]);self.assertEqual(self.sdk.count('put'),puts)
        archives=list(self.receipts.glob('*.prior-*.json'));self.assertEqual(len(archives),1);self.assertEqual(archives[0].read_bytes(),original)
        current=json.loads(receipt.read_text());self.assertEqual(current['pathname'],old['pathname']);self.assertEqual(current['key'],old['key'])
        self.assertEqual(self.invoke('recover-absent')[0],2)
        self.assertEqual(self.invoke()[1]['status'],'media_processing');self.due()
        self.assertEqual(self.invoke()[1]['status'],'cleaned');self.assertEqual(self.sdk.count('put'),puts+1)
        self.assertEqual(archives[0].read_bytes(),original)

    def test_uncertain_continuation_cannot_authorize_another_absent_put(self):
        self.put_fail=True;self.invoke()
        self.assertEqual(self.invoke('recover-absent')[1]['status'],'absent_continuation_prepared')
        self.assertEqual(self.invoke()[0],2);puts=self.sdk.count('put')
        self.assertEqual(self.invoke('recover-absent')[0],2)
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk.count('put'),puts);self.assertEqual(self.api,[])
        self.assertEqual(len(list(self.receipts.glob('*.prior-*.json'))),1)

    def test_absence_requires_complete_authentic_same_prefix_and_signing(self):
        self.put_fail=True;self.invoke();original_call=self.sdk_call
        receipt=next(self.receipts.glob('*.json'));original=receipt.read_bytes();puts=self.sdk.count('put')
        for bad in ('present','partial','wrong-prefix','auth','sign'):
            def invalid(action,body,fd=None):
                if bad=='sign' and action=='sign':raise TimeoutError()
                if action=='absent':
                    if bad=='auth':raise PermissionError()
                    return {'absent':bad!='present','complete':bad!='partial','prefix':'other' if bad=='wrong-prefix' else body['pathname'],'pages':1}
                return original_call(action,body,fd)
            self.sdk_call=invalid
            self.assertEqual(self.invoke('recover-absent')[0],2)
            self.assertEqual(receipt.read_bytes(),original)
        self.assertEqual(self.sdk.count('put'),puts);self.assertEqual(self.api,[])
        self.assertEqual(list(self.receipts.glob('*.prior-*.json')),[])

    def test_absence_rejects_existing_meta_job_or_changed_marker(self):
        self.put_fail=True;self.invoke();puts=self.sdk.count('put');count=len(self.sdk)
        ledger=p.Ledger(self.state)
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,content_hash,state,updated_at) VALUES(?,?,?,?,'unknown',0)",('threads','123',self.job,'synthetic'))
        ledger.close()
        self.assertEqual(self.invoke('recover-absent')[0],2);self.assertEqual(self.sdk.count('put'),puts)
        self.assertEqual(len(self.sdk),count);self.assertEqual(self.api,[])

    def test_absence_changed_runtime_marker_and_job_lock_block(self):
        import fcntl
        self.put_fail=True;self.invoke();count=len(self.sdk)
        lock=next(self.receipts.glob('*.lock'));fd=os.open(lock,os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.invoke('recover-absent')[0],2)
        finally:os.close(fd)
        marker=self.state/'runtime.json';data=json.loads(marker.read_text());data['runtime_id']='different-runtime';marker.write_text(json.dumps(data))
        self.assertEqual(self.invoke('recover-absent')[0],2);self.assertEqual(len(self.sdk),count);self.assertEqual(self.api,[])

    def test_absence_changed_approval_does_not_read_or_write_provider(self):
        self.put_fail=True;self.invoke();count=len(self.sdk)
        approval=json.loads(self.approval.read_text());approval['approval_record']='different';self.approval.write_text(json.dumps(approval))
        self.assertEqual(self.invoke('recover-absent')[0],2);self.assertEqual(len(self.sdk),count);self.assertEqual(self.api,[])

    def test_wrong_bytes_keep_blob_without_publication(self):
        self.fetch_fail=True;self.assertEqual(self.invoke()[0],2)
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk.count('put'),1)
        self.assertNotIn('delete',self.sdk);self.assertEqual(self.api,[])

    def test_default_off_and_plan_do_not_write_or_call_provider(self):
        self.assertEqual(self.invoke(enabled=False)[0],2)
        self.assertEqual(self.invoke('plan')[1]['status'],'review_only')
        self.assertEqual(list(self.receipts.iterdir()),[]);self.assertEqual(self.sdk,[]);self.assertEqual(self.api,[])

    def test_recovered_threads_admission_is_explicitly_unsupported(self):
        original=p.Ledger
        def recovered(*a,**kw):
            ledger=original(*a,**kw);ledger.recovered=True;return ledger
        with mock.patch.object(p,'Ledger',side_effect=recovered):
            code,result=self.invoke()
        self.assertEqual(code,2);self.assertIn('admission unsupported',result['error'])
        self.assertEqual(self.sdk,[]);self.assertEqual(self.api,[]);self.assertEqual(list(self.receipts.iterdir()),[])

    def test_sign_failure_is_durable_pre_put_and_retryable_without_put(self):
        original=self.sdk_call
        def fail_sign(action,body,fd=None):
            if action=='sign':raise TimeoutError('must not expose provider details')
            return original(action,body,fd)
        self.sdk_call=fail_sign
        self.assertEqual(self.invoke()[0],2)
        receipt=next(self.receipts.glob('*.json'));record=json.loads(receipt.read_text())
        self.assertEqual(record['state'],'preparing');self.assertEqual(record['error'],'pre_put_preparation_failed')
        self.assertNotIn('put',self.sdk);self.assertEqual(self.api,[])
        self.sdk_call=original
        self.assertEqual(self.invoke()[1]['status'],'media_processing')
        self.assertEqual(self.sdk.count('put'),1)

    def test_crash_after_preparation_before_put_fence_retries_only_preparation(self):
        original=b.save
        def crash(path,record):
            original(path,record)
            if record['state']=='preparing':raise SystemExit('synthetic preparation crash')
        with mock.patch.object(b,'save',side_effect=crash),self.assertRaises(SystemExit):self.invoke()
        self.assertNotIn('put',self.sdk)
        self.assertEqual(self.invoke()[1]['status'],'media_processing');self.assertEqual(self.sdk.count('put'),1)

    def test_durable_put_fence_crash_never_retries_put(self):
        original=b.save
        def crash(path,record):
            original(path,record)
            if record['state']=='upload_started':raise SystemExit('synthetic fenced crash')
        with mock.patch.object(b,'save',side_effect=crash),self.assertRaises(SystemExit):self.invoke()
        self.assertEqual(self.invoke()[0],2);self.assertNotIn('put',self.sdk);self.assertEqual(self.api,[])

    def test_wrong_store_or_expiry_blocks_before_put(self):
        original=self.sdk_call
        def wrong(action,body,fd=None):
            result=original(action,body,fd)
            if action=='sign':result['url']=result['url'].replace(self.host,'wrong.private.blob.vercel-storage.com')
            return result
        self.sdk_call=wrong
        self.assertEqual(self.invoke()[0],2);self.assertNotIn('put',self.sdk);self.assertEqual(self.api,[])

    def test_unconfirmed_final_get_never_deletes(self):
        self.invoke();self.due();self.delete_fail=True
        self.assertEqual(self.invoke()[1]['status'],'cleanup_pending')
        count=self.sdk.count('delete');original=self.Provider.request
        def unavailable(client,method,path,**kw):
            if path=='/900':raise p.ApiError()
            return original(client,method,path,**kw)
        with mock.patch.object(self.Provider,'request',unavailable):self.assertEqual(self.invoke()[0],2)
        self.assertEqual(self.sdk.count('delete'),count)
        self.assertEqual(sum(c[1]=='/123/threads_publish' for c in self.api),1)

    def test_accepted_delete_cache_verification_never_redeletes(self):
        self.invoke();self.due();original=self.sdk_call
        def delayed(action,body,fd=None):
            result=original(action,body,fd)
            return {'deleted':False,'verificationPending':True} if action=='delete' else result
        self.sdk_call=delayed
        self.assertEqual(self.invoke()[1]['status'],'cleanup_verify_pending')
        previous=len(self.api)
        self.assertEqual(self.invoke()[1]['status'],'cleaned')
        self.assertEqual(self.sdk.count('delete'),1);self.assertEqual(self.sdk.count('verify_delete'),1)
        self.assertTrue(all(c[0]=='GET' for c in self.api[previous:]))

    def test_other_object_path_cannot_be_cleaned(self):
        self.invoke();self.due();self.delete_fail=True;self.invoke()
        receipt=next(p for p in self.receipts.glob('*.json') if not p.name.endswith('.delivery.json'))
        data=json.loads(receipt.read_text());data['pathname']='moru-native/'+'f'*64+'.mp4';receipt.write_text(json.dumps(data))
        count=len(self.sdk)
        self.assertEqual(self.invoke()[0],2);self.assertEqual(len(self.sdk),count)

    def test_single_job_lock_prevents_parallel_resume(self):
        import fcntl
        self.invoke();lock=next(self.receipts.glob('*.lock'));fd=os.open(lock,os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);calls=len(self.api);sdk_calls=len(self.sdk)
            self.assertEqual(self.invoke()[0],2)
            self.assertEqual(len(self.api),calls);self.assertEqual(len(self.sdk),sdk_calls)
        finally:os.close(fd)

    def test_sdk_subprocess_gets_only_dedicated_secret_in_environment(self):
        result=mock.Mock(returncode=0,stdout='{"supported":true}')
        with mock.patch.dict(os.environ,self.env,clear=True),mock.patch.object(b.subprocess,'run',return_value=result) as call:
            self.assertTrue(b.sdk_call('check',{'pathname':'moru-native/'+'a'*64+'.mp4','sha256':'a'*64})['supported'])
        args,kwargs=call.call_args
        self.assertNotIn('synthetic-blob-secret',repr(args));self.assertNotIn('synthetic-blob-secret',kwargs['input'])
        self.assertNotIn('THREADS_USER_ACCESS_TOKEN',kwargs['env']);self.assertNotIn('VERCEL_OIDC_TOKEN',kwargs['env'])
        self.assertEqual(kwargs['env']['MORU_BLOB_READ_WRITE_TOKEN'],'synthetic-blob-secret')

    def test_managed_ca_reaches_child_without_insecure_node_options(self):
        result=mock.Mock(returncode=0,stdout='{"supported":true}')
        env={**self.env,'NODE_EXTRA_CA_CERTS':'/synthetic/managed-ca.pem','SSL_CERT_FILE':'/synthetic/system.pem',
             'NODE_OPTIONS':'--inspect','NODE_TLS_REJECT_UNAUTHORIZED':'0'}
        with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(b.subprocess,'run',return_value=result) as call:
            b.sdk_call('check',{})
        args,kwargs=call.call_args
        self.assertEqual(args[0][1],'--use-system-ca')
        self.assertEqual(kwargs['env']['NODE_EXTRA_CA_CERTS'],env['NODE_EXTRA_CA_CERTS'])
        self.assertEqual(kwargs['env']['SSL_CERT_FILE'],env['SSL_CERT_FILE'])
        self.assertNotIn('NODE_OPTIONS',kwargs['env']);self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED',kwargs['env'])

    def test_sdk_errors_only_expose_allowlisted_categories(self):
        for supplied,expected in [('tls_validation_failed','tls_validation_failed'),('private-url-and-token','blob_operation_unconfirmed')]:
            result=mock.Mock(returncode=2,stdout=json.dumps({'error':supplied}))
            with mock.patch.dict(os.environ,self.env,clear=True),mock.patch.object(b.subprocess,'run',return_value=result):
                with self.assertRaises(p.SafeError) as caught:b.sdk_call('head',{})
            self.assertEqual(caught.exception.blob_category,expected)
            self.assertNotIn('private-url-and-token',str(caught.exception))

    def test_wrong_source_and_account_block_before_upload(self):
        doc=json.loads(self.approval.read_text());doc['account_id']='999';self.approval.write_text(json.dumps(doc))
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk,[])
        doc['account_id']='123';doc['slack_channel']='DUNAPPROVED';self.approval.write_text(json.dumps(doc))
        self.assertEqual(self.invoke()[0],2);self.assertEqual(self.sdk,[])

if __name__=='__main__':unittest.main()
