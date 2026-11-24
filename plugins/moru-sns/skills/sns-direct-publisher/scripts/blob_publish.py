"""Opt-in native private Blob delivery. No server and no automatic setup or schedules."""
import argparse
import contextlib
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import urllib.parse

import publisher as p
import publication_profile as profiles
import candidate_review as review
import video_preflight
import delivery_binding


@contextlib.contextmanager
def approval_environment(path):
    prior=os.environ.get('MORU_PRIVATE_BLOB_APPROVAL')
    try:
        need(bool(path));os.environ['MORU_PRIVATE_BLOB_APPROVAL']=str(path)
        yield
    finally:
        if prior is None:os.environ.pop('MORU_PRIVATE_BLOB_APPROVAL',None)
        else:os.environ['MORU_PRIVATE_BLOB_APPROVAL']=prior


def need(ok):
    if not ok:raise p.SafeError('Private Blob configuration, approval or receipt does not match.')


def sdk_call(action,body,fd=None):
    token=os.environ.get('MORU_BLOB_READ_WRITE_TOKEN')
    need(bool(token))
    # No ambient project/OIDC credentials, shell, CLI login, env file, or token arguments.
    env={k:v for k,v in os.environ.items() if k in ('PATH','SSL_CERT_FILE','SSL_CERT_DIR','NODE_EXTRA_CA_CERTS','HTTPS_PROXY','https_proxy','NO_PROXY','no_proxy')}
    env['MORU_BLOB_READ_WRITE_TOKEN']=token
    bridge=Path(__file__).parent/'blob-sdk'/'bridge.mjs'
    try:
        result=subprocess.run(['node','--use-system-ca',str(bridge)],input=json.dumps({'action':action,**body}),
            text=True,capture_output=True,timeout=150,env=env,pass_fds=() if fd is None else (fd,))
    except subprocess.TimeoutExpired:
        error=p.SafeError('Private Blob request timed out.');error.blob_category='request_timeout_or_aborted';raise error from None
    # stdout may contain an ephemeral signed URL; neither stream is logged.
    need(len(result.stdout)<=65536)
    value=json.loads(result.stdout);need(isinstance(value,dict))
    if result.returncode!=0 or 'error' in value:
        category=value.get('error')
        if category not in ('tls_validation_failed','connection_refused','request_timeout_or_aborted'):category='blob_operation_unconfirmed'
        error=p.SafeError('Private Blob transport: '+category);error.blob_category=category;raise error
    return value


def save(path,record):
    tmp=path.with_name(path.name+'.'+secrets.token_hex(8))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as out:
            out.write(review.canonical(record));out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if tmp.exists():tmp.unlink()


def config():
    path=os.environ.get('MORU_PRIVATE_BLOB_CONFIG')
    if not path:raise p.SafeError('Private Blob delivery is disabled (default off).')
    c=profiles.read_private(path)
    review.keys(c,'version enabled store_host receipt_dir approved_slack_channels')
    need(c['version']==1 and c['enabled'] is True)
    need(isinstance(c['store_host'],str) and re.fullmatch(r'[a-z0-9-]+\.private\.blob\.vercel-storage\.com',c['store_host']))
    need(isinstance(c['approved_slack_channels'],list) and c['approved_slack_channels'])
    root=p.state_path(c['receipt_dir']);p.check_state_node(root,directory=True)
    return c,root


def candidate(args,c,*,resuming=False):
    doc=profiles.read_private(args.approval)
    review.keys(doc,'version platform account_id job_id media_sha256 content_sha256 slack_channel slack_file_id slack_message_ts approval_record final_original ai_generated')
    need(doc['version']==1 and doc['platform']=='threads' and doc['final_original'] is True and type(doc['ai_generated']) is bool)
    need(doc['slack_channel'] in c['approved_slack_channels'])
    need(re.fullmatch(r'F[A-Z0-9]+',doc['slack_file_id']) and re.fullmatch(r'[0-9]+\.[0-9]+',doc['slack_message_ts']))
    review.identifier(doc['job_id']);review.identifier(doc['approval_record']);review.sha(doc['media_sha256'])
    text=p.validate_text('threads',Path(args.text_file).read_text())
    media=p.prepare_video('threads',args.video_file,ai_generated=doc['ai_generated'],allow_missing_url=True)
    need(doc['job_id']=='capture-'+media.sha256)
    need(media.sha256==doc['media_sha256'] and p.content_hash(text,media)==doc['content_sha256'])
    need(not video_preflight.inspect('threads',media)['local']['reasons'])
    profile=profiles.configured();binding=None
    if profile:
        approved=profiles.bind(profile,args.publication_evidence,args.source_file,'threads',text,media,confirming=resuming)
        need(approved['job_id']==doc['job_id'])
        binding=approved['binding_sha256']
        need(args.reviewed_hash==p.content_hash(text,media,publication_binding=binding))
    need(doc['account_id']==os.environ.get('THREADS_EXPECTED_USER_ID'))
    p.require_state_binding(args.state_dir,'threads',doc['account_id'])
    return doc,text,media,p.content_hash(text,media,publication_binding=binding)


def validate_url(url,c,pathname,signed=False,expiry=None):
    p.public_video_url(url);u=urllib.parse.urlsplit(url)
    need(u.hostname==c['store_host'] and u.path=='/'+pathname)
    if signed:
        q=urllib.parse.parse_qs(u.query)
        need(q.get('vercel-blob-valid-until')==[str(expiry)])
    else:need(not u.query)


def execute(args):
    c,root=config()
    preview=profiles.read_private(args.approval)
    ledger=p.Ledger(args.state_dir)
    try:
        saved_job=ledger.get(('threads',preview['account_id'],preview['job_id']))
        resuming=bool(saved_job)
        doc,text,media,content=candidate(args,c,resuming=resuming)
        key=('threads',doc['account_id'],doc['job_id'])
        if ledger.recovered and not (saved_job and saved_job['state']=='published'):
            if profiles.configured() is None:raise p.SafeError('Threads recovered-ledger candidate admission unsupported without an exact profile approval; no Blob upload attempted.')
            import profile_recovery
            approved=profiles.bind(profiles.configured(),args.publication_evidence,args.source_file,'threads',text,media,confirming=resuming)
            saved=profile_recovery.load(ledger,key,content,media.sha256,approved['binding_sha256'])
            need(saved['version']==3)
            # Fresh complete owned history/absence review before an upload, not only before POST.
            if not resuming and args.command=='publish':
                profile_recovery.prepare(ledger,key,content,media,approved['binding_sha256'],profiles.configured(),p.APIClient('threads',os.environ.get('THREADS_USER_ACCESS_TOKEN','')))
        marker=p.read_state_marker(ledger.directory/'runtime.json')
        ident=review.digest(review.canonical([marker['runtime_id'],*key]))
        path=root/(ident+'.json');lock=root/(ident+'.lock')
        if args.command=='plan':
            return {'status':'review_only','blob_enabled':True,'publishing_attempted':False,'state_writes':0,
                    'media_sha256':media.sha256,'native_admission':'exact_recovered_profile' if ledger.recovered else 'standard_native','delivery_verified':False}
        need(args.execute or args.command=='recover')
        need(bool(os.environ.get('THREADS_USER_ACCESS_TOKEN')) and bool(os.environ.get('MORU_BLOB_READ_WRITE_TOKEN')))
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            p.check_state_node(lock);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return locked(args,c,doc,text,media,content,ledger,key,path,ident)
        finally:os.close(fd)
    finally:ledger.close()


def locked(args,c,doc,text,media,content,ledger,key,path,ident):
    approved=review.digest(review.canonical(doc))
    record=profiles.read_private(path) if path.exists() else None
    if record:
        need(record['approval_sha256']==approved and record['content_sha256']==content and record['store_host']==c['store_host'])
    old=ledger.get(key)
    signed_cache=None
    if old is None:
        need(not ledger.db.execute("SELECT 1 FROM jobs WHERE platform=? AND account_id=? AND content_hash=? AND state NOT IN ('failed','duplicate')",(*key[:2],content)).fetchone())
    if record is None:
        need(old is None and args.command=='publish')
        capability=sdk_call('check',{'pathname':'moru-native/'+ident+'.mp4','sha256':media.sha256})
        need(capability.get('supported') is True)
        record={'version':1,'state':'preparing','approval_sha256':approved,'content_sha256':content,
                'media_sha256':media.sha256,'store_host':c['store_host'],'pathname':'moru-native/'+ident+'.mp4',
                'started_at':time.time(),'key':list(key)}
        save(path,record)  # No PUT is possible while this durable state remains preparing.
    need(record['key']==list(key) and record['media_sha256']==media.sha256 and record['pathname']=='moru-native/'+ident+'.mp4')
    if args.command=='recover-absent':
        return recover_absent(c,record,path,media,ledger,key,content)
    if record['state']=='preparing':
        need(old is None and args.command=='publish')
        if record.get('absence_continuation'):
            prior_sha=record['absence_continuation']['prior_receipt_sha256'];review.sha(prior_sha)
            archive=path.with_name(path.stem+'.prior-'+prior_sha+'.json');p.check_state_node(archive)
            need(hashlib.sha256(archive.read_bytes()).hexdigest()==prior_sha)
        upload_fd=os.open(media.path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            media.verify_for_publish()
            expiry=int((time.time()+900)*1000)
            signed_cache=sdk_call('sign',{'pathname':record['pathname'],'sha256':media.sha256,'validUntil':expiry})
            need(signed_cache['operation']=='get' and signed_cache['validUntil']==expiry)
            validate_url(signed_cache['url'],c,record['pathname'],True,expiry)
            need(p.file_identity(os.fstat(upload_fd))==p.file_identity(media.path.stat()))
            record['state']='upload_started';save(path,record)  # Durable fence before dispatching PUT.
            blob=sdk_call('put',{'pathname':record['pathname'],'sha256':media.sha256,'bytes':media.size,'fd':upload_fd},upload_fd)
            validate_url(blob['url'],c,record['pathname'])
            need(blob['pathname']==record['pathname'] and blob['contentType']=='video/mp4' and isinstance(blob['etag'],str) and blob['etag'])
            record.update(state='uploaded',base_url=blob['url'],etag=blob['etag']);save(path,record)
        except Exception as exc:
            record['transport_error']=getattr(exc,'blob_category','blob_operation_unconfirmed')
            if record['state']=='preparing':
                record['error']='pre_put_preparation_failed';save(path,record)
                raise p.SafeError('Blob preparation failed before PUT; same approved preparation may be retried.') from None
            record['state']='upload_unknown';save(path,record)
            raise p.SafeError('Blob upload outcome uncertain; source and receipt retained, no upload retry.') from None
        finally:os.close(upload_fd)
    need(record['key']==list(key) and record['media_sha256']==media.sha256 and record['pathname']=='moru-native/'+ident+'.mp4')
    if 'base_url' in record:validate_url(record['base_url'],c,record['pathname'])
    if args.command=='recover' and old and old['state']=='published' and not record.get('etag'):
        recover_object(c,record,path,media)
    if args.command=='recover' and (not old or old['state']!='published'):
        need(old is None)  # Any prior provider attempt stays in its native GET-only flow.
        return recover_object(c,record,path,media)
    if record['state'] in ('upload_started','upload_unknown','verification_unknown') and not (old and old['state']=='published' and record.get('etag')):
        raise p.SafeError('Blob operation unresolved; use exact-object recover; no upload or publication retry.')
    if record['state']=='cleaned':
        return {'status':'cleaned','publication_requests_skipped':True,'job_id':key[2]}
    client=p.APIClient('threads',os.environ.get('THREADS_USER_ACCESS_TOKEN',''))
    engine=p.Publisher('threads',key[1],client,ledger,source_file=args.source_file,
        publication_evidence=args.publication_evidence,reviewed_hash=args.reviewed_hash)
    if old and old['state']=='unknown':
        # Read-only owned history is useful evidence but cannot identify a lost receipt by guess.
        rows=review.owned('threads',p.read_state_marker(ledger.directory/'runtime.json')['accounts']['threads'],client)
        return {'status':'publication_unknown','owned_history_read':True,'posts_seen':len(rows),
                'blob_retained':True,'publication_requests_skipped':True}
    if old and old['state']=='published':
        return cleanup(c,record,path,ledger,key,content,client,engine)
    if old and old['state'] not in ('media_processing','confirming_label','checking_media'):
        return {'status':'publication_held','blob_retained':True,'publication_requests_skipped':True}
    approval_file=path.with_suffix('.delivery.json')
    if old is None:
        expiry=signed_cache['validUntil'] if signed_cache else int((time.time()+900)*1000)
        try:
            signed=signed_cache or sdk_call('sign',{'pathname':record['pathname'],'sha256':media.sha256,'validUntil':expiry})
            need(signed['operation']=='get' and signed['validUntil']==expiry and time.time()*1000<expiry)
            validate_url(signed['url'],c,record['pathname'],True,expiry)
            delivery_binding.remote_bytes(signed['url'],media.size,media.sha256,approved_host=c['store_host'],maximum_size=p.MAX_VIDEO_BYTES)
            delivered=replace(media,url=signed['url'])
            approval={'version':1,'platform':'threads','account_id':key[1],'job_id':key[2],
                'media_sha256':media.sha256,'url_sha256':review.digest(signed['url']),
                'content_sha256':p.content_hash(text,media),'approval_record':doc['approval_record'],
                'final_original':True,'expires_at':expiry/1000}
            save(approval_file,approval)
            record.update(state='verified',url_sha256=approval['url_sha256'],url_expires_at=expiry/1000);save(path,record)
        except Exception:
            record['state']='verification_unknown';save(path,record)
            raise p.SafeError('Blob signing or byte verification unresolved; object/receipt retained, no publication.') from None
    else:
        # Existing container resume never needs another PUT or a new publicly usable URL.
        delivered=replace(media,url=record['base_url'])
    prior=os.environ.get('MORU_VIDEO_URL_APPROVAL')
    try:
        os.environ['MORU_VIDEO_URL_APPROVAL']=str(approval_file)
        result=engine.publish(key[2],text,delivered)
    finally:
        if prior is None:os.environ.pop('MORU_VIDEO_URL_APPROVAL',None)
        else:os.environ['MORU_VIDEO_URL_APPROVAL']=prior
    if result.get('state')=='published':return cleanup(c,record,path,ledger,key,content,client,engine)
    return {'status':result.get('state','held'),'blob_retained':True,'job_id':key[2],'post_id':result.get('post_id')}


def recover_absent(c,record,path,media,ledger,key,content):
    # Explicit continuation, never an upload. Keep the job lock and DB write lock
    # across evidence collection so another native publisher cannot claim this job.
    need(record['version']==1 and record['state'] in ('upload_started','upload_unknown'))
    need(not any(record.get(k) for k in ('etag','base_url','post_id','container_id','absence_continuation')))
    ledger.db.execute('BEGIN IMMEDIATE')
    try:
        need(ledger.get(key) is None)
        need(not ledger.db.execute('SELECT 1 FROM jobs WHERE platform=? AND account_id=? AND content_hash=?',(*key[:2],content)).fetchone())
        original=path.read_bytes();original_sha=hashlib.sha256(original).hexdigest()
        need(json.loads(original)==record)
        expiry=int((time.time()+900)*1000)
        body={'pathname':record['pathname'],'sha256':media.sha256}
        signed=sdk_call('sign',{**body,'validUntil':expiry})
        need(signed['operation']=='get' and signed['validUntil']==expiry)
        validate_url(signed['url'],c,record['pathname'],True,expiry)
        evidence=sdk_call('absent',body)
        need(evidence.get('absent') is True and evidence.get('complete') is True and evidence.get('prefix')==record['pathname'])
        need(type(evidence.get('pages')) is int and 1<=evidence['pages']<=100 and time.time()*1000<expiry)
        need(ledger.get(key) is None and path.read_bytes()==original)
        archive=path.with_name(path.stem+'.prior-'+original_sha+'.json')
        try:
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            p.check_state_node(archive);need(archive.read_bytes()==original)
        else:
            with os.fdopen(fd,'wb') as out:out.write(original);out.flush();os.fsync(out.fileno())
        # save fsyncs the containing directory after the archive and new record.
        record.update(state='preparing',absence_continuation={'prior_receipt_sha256':original_sha,
            'observed_at':time.time(),'pages':evidence['pages'],'same_path_no_overwrite':True})
        save(path,record)
        ledger.db.execute('COMMIT')
        return {'status':'absent_continuation_prepared','publication_requests_skipped':True,'upload_requests_skipped':True,
                'prior_receipt_sha256':original_sha}
    except Exception:
        if ledger.db.in_transaction:ledger.db.execute('ROLLBACK')
        raise


def recover_object(c,record,path,media):
    # Explicit read-only recovery: never PUT, DELETE, or call the publisher here.
    need(record['state'] in ('upload_started','upload_unknown','verification_unknown','uploaded','verified'))
    expiry=int((time.time()+900)*1000)
    body={'pathname':record['pathname'],'sha256':media.sha256}
    signed=sdk_call('sign',{**body,'validUntil':expiry})
    need(signed['operation']=='get' and signed['validUntil']==expiry)
    validate_url(signed['url'],c,record['pathname'],True,expiry)
    def head():
        value=sdk_call('head',body)
        validate_url(value['url'],c,record['pathname'])
        need(value['pathname']==record['pathname'] and value['size']==media.size and value['contentType']=='video/mp4')
        need(isinstance(value['etag'],str) and bool(value['etag']))
        if record.get('etag'):need(record['etag']==value['etag'])
        return value
    before=head()
    delivery_binding.remote_bytes(signed['url'],media.size,media.sha256,approved_host=c['store_host'],maximum_size=p.MAX_VIDEO_BYTES)
    after=head()
    need(before==after and time.time()*1000<expiry)
    record.update(state='uploaded',base_url=after['url'],etag=after['etag'],
                  recovered_at=time.time(),recovered_sha256=media.sha256)
    save(path,record)
    return {'status':'object_recovered','publication_requests_skipped':True,'upload_requests_skipped':True}


def cleanup(c,record,path,ledger,key,content,client,engine):
    row=ledger.get(key)
    need(row and row['state']=='published' and row['content_hash']==content and row['post_id'] and row['permalink'])
    engine.check()
    receipt=p.api_dict(client.request('GET','/'+p.api_id(row['post_id']),params={'fields':'id,media_type,permalink'}))
    need(receipt.get('id')==row['post_id'] and receipt.get('media_type')=='VIDEO' and receipt.get('permalink')==row['permalink'])
    verify_only=record['state']=='cleanup_verify_pending'
    record.update(state='cleanup_verify_pending' if verify_only else 'cleanup_pending',post_id=row['post_id'],permalink=row['permalink']);save(path,record)
    try:
        expiry=int((time.time()+300)*1000)
        proof=sdk_call('sign',{'pathname':record['pathname'],'sha256':record['media_sha256'],'validUntil':expiry})
        need(proof['operation']=='get' and proof['validUntil']==expiry)
        validate_url(proof['url'],c,record['pathname'],True,expiry)
        deleted=sdk_call('verify_delete' if verify_only else 'delete',{'pathname':record['pathname'],'sha256':record['media_sha256'],'etag':record['etag']})
        if deleted.get('verificationPending') is True:
            record['state']='cleanup_verify_pending';save(path,record)
            return {'status':'cleanup_verify_pending','post_id':row['post_id'],'publication_requests_skipped':True,'cleanup_verification_only':True}
        need(deleted.get('deleted') is True)
    except Exception:
        return {'status':record['state'],'post_id':row['post_id'],'publication_requests_skipped':True,'blob_retained_or_delete_unconfirmed':True}
    record['state']='cleaned';save(path,record)
    return {'status':'cleaned','post_id':row['post_id'],'permalink':row['permalink'],'publication_requests_skipped':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','publish','recover','recover-absent'])
    for flag in ('state-dir','approval','video-file','text-file'):parser.add_argument('--'+flag,required=True)
    for flag in ('source-file','publication-evidence','reviewed-hash'):parser.add_argument('--'+flag)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args(argv)
    try:
        with approval_environment(args.approval):result=execute(args)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['status'] in ('review_only','cleaned','object_recovered','absent_continuation_prepared') else 3
    except Exception as exc:
        print(json.dumps({'status':'blocked','error':str(exc) if isinstance(exc,p.SafeError) else 'Private Blob operation unconfirmed; preserve receipts and do not retry publication.'}));return 2

if __name__=='__main__':raise SystemExit(main())
