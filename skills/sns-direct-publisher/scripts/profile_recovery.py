"""Append-only exact profile candidate admission in an existing native recovery ledger."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import publisher as p
import candidate_review as review
import publication_profile as profiles
import receipt_absence as absence


def record_key(key):
    return 'profile_candidate:'+':'.join(key)


def jobs_digest(ledger, key):
    rows=[dict(r) for r in ledger.db.execute('SELECT * FROM jobs ORDER BY platform,account_id,job_id')
          if (r['platform'],r['account_id'],r['job_id']) != key]
    return review.digest(review.canonical(rows))


def legacy_digest(ledger):
    rows=[dict(r) for r in ledger.db.execute('SELECT * FROM recovery_legacy_attempts ORDER BY platform,job_id')]
    return review.digest(review.canonical(rows))


def require_route(platform, marker, media):
    if platform != 'instagram' or marker['accounts'].get(platform,{}).get('auth_mode') != 'unified':
        raise p.SafeError('Native recovery profile admission currently supports Instagram Unified resumable only; other platform delivery proof is not registered.')
    review.require(isinstance(media,p.Video) and media.url is None and media.preflight=='verified_local_profile')


def validate(ledger, doc, key, digest, media_sha, binding):
    review.keys(doc,'version runtime_id platform account_id job_id source_sha256 media_sha256 profile_binding_sha256 content_hash base_content_hash history prior_jobs_sha256 legacy_attempts_sha256 candidate_never_attempted single_writer_confirmed approval_record distinctness_report_sha256 pending_exclusions' + (' external_absences' if doc.get('version')==2 else ''))
    marker=p.read_state_marker(ledger.directory/'runtime.json')
    review.require(type(doc['version']) is int and doc['version'] in (1,2) and doc['runtime_id']==marker['runtime_id'])
    review.require((doc['platform'],doc['account_id'],doc['job_id'])==key)
    review.require(marker['accounts'].get(key[0],{}).get('account_id')==key[1])
    review.require(doc['candidate_never_attempted'] is True and doc['single_writer_confirmed'] is True)
    review.identifier(doc['approval_record'])
    for field in ('source_sha256','media_sha256','profile_binding_sha256','content_hash','base_content_hash','prior_jobs_sha256','legacy_attempts_sha256','distinctness_report_sha256'):
        review.sha(doc[field])
    review.require(key[2]=='capture-'+doc['source_sha256'])
    review.require(doc['content_hash']==digest and doc['media_sha256']==media_sha and doc['profile_binding_sha256']==binding)
    provenance=json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])
    review.require(provenance['old_writers_stopped'] is True)
    review.require(doc['prior_jobs_sha256']==jobs_digest(ledger,key) and doc['legacy_attempts_sha256']==legacy_digest(ledger))
    # Only specifically reviewed legacy unknowns may remain; active attempts never qualify.
    unresolved=[dict(r) for r in ledger.db.execute("SELECT * FROM jobs WHERE state NOT IN ('published','failed','duplicate') ORDER BY platform,account_id,job_id")
                if (r['platform'],r['account_id'],r['job_id'])!=key]
    review.require(all(r['state']=='unknown' for r in unresolved))
    expected={(r['platform'],r['account_id'],r['job_id']) for r in unresolved}
    declared=set()
    for item in doc['pending_exclusions']:
        review.keys(item,'platform account_id job_id report_sha256');review.sha(item['report_sha256'])
        declared.add((item['platform'],item['account_id'],item['job_id']))
    review.require(len(declared)==len(doc['pending_exclusions']) and declared==expected)
    for row in ledger.db.execute('SELECT * FROM jobs'):
        if (row['platform'],row['account_id'],row['job_id'])!=key:
            review.require(row['content_hash'] not in (digest,doc['base_content_hash']))
    for row in ledger.db.execute('SELECT document FROM recovery_legacy_attempts'):
        attempt=json.loads(row[0])
        review.require(attempt.get('media_sha256') not in (doc['source_sha256'],media_sha) and attempt.get('content_hash') not in (digest,doc['base_content_hash']))
    if ledger.db.execute("SELECT 1 FROM sqlite_master WHERE name='candidate_pair'").fetchone():
        for row in ledger.db.execute('SELECT document FROM candidate_pair'):
            review.require(json.loads(row[0])['source']['sha256']!=doc['source_sha256'])
    for sha in {doc['source_sha256'],media_sha}:
        row=ledger.db.execute('SELECT job_id FROM recovery_media WHERE platform=? AND sha256=?',(key[0],sha)).fetchone()
        review.require(row is None or row[0]==key[2])
    if doc['version']==2:absence.validate(ledger,doc['external_absences'],key)
    return doc


def match_history(ledger,rows,doc,key,clients=None):
    if doc['version']==2:absence.match(ledger,rows,doc['external_absences'],key,clients)
    else:review.match_ledger(ledger,rows)


def load(ledger,key,digest,media_sha,binding):
    row=ledger.db.execute('SELECT value FROM recovery_metadata WHERE key=?',(record_key(key),)).fetchone()
    review.require(row is not None)
    envelope=json.loads(row[0]);doc=envelope['document']
    review.require(review.digest(review.canonical(doc))==envelope['sha256'])
    if doc['version']==2:
        for item in doc['external_absences']:
            if item['immediate_replacement']:
                owner=ledger.db.execute('SELECT value FROM recovery_metadata WHERE key=?',(absence.reservation(item),)).fetchone()
                review.require(owner is not None and owner[0]==review.canonical(key))
    return validate(ledger,doc,key,digest,media_sha,binding)


def register(directory,doc,media,text,source_path,evidence_path,artifacts_dir):
    profile=profiles.configured();review.require(profile is not None)
    approval=profiles.bind(profile,evidence_path,source_path,doc['platform'],text,media)
    digest=p.content_hash(text,media,publication_binding=approval['binding_sha256'])
    review.require(doc['base_content_hash']==p.content_hash(text,media))
    key=(doc['platform'],doc['account_id'],approval['job_id'])
    ledger=p.Ledger(directory)
    try:
        review.require(ledger.recovered)
        marker=p.read_state_marker(ledger.directory/'runtime.json');require_route(key[0],marker,media)
        validate(ledger,doc,key,digest,media.sha256,approval['binding_sha256'])
        review.require(ledger.get(key) is None)
        review.require(not ledger.db.execute('SELECT 1 FROM recovery_metadata WHERE key=?',(record_key(key),)).fetchone())
        artifacts=p.state_path(str(Path(artifacts_dir)));p.check_state_node(artifacts,directory=True)
        for sha in [doc['distinctness_report_sha256'],*[i['report_sha256'] for i in doc['pending_exclusions']],*[i['report_sha256'] for i in doc.get('external_absences',[])]]:
            review.require(review.file_digest(artifacts/sha)==sha)
        rows=review.collect(marker['accounts']);match_history(ledger,rows,doc,key)
        review.require(review.snapshot_digest(rows)==review.snapshot_digest(doc['history']))
        for row in rows:
            if (row['platform'],row['account_id'])==key[:2]:
                review.require(time.time()-row['published_at'] >= profile['minimum_publish_interval_hours']*3600)
        envelope=review.canonical({'document':doc,'sha256':review.digest(review.canonical(doc))})
        ledger.db.execute('BEGIN IMMEDIATE')
        validate(ledger,doc,key,digest,media.sha256,approval['binding_sha256'])
        if doc['version']==2:absence.reserve(ledger,doc['external_absences'],key)
        ledger.db.execute('INSERT INTO recovery_metadata VALUES(?,?)',(record_key(key),envelope))
        ledger.db.execute('COMMIT')
        return {'status':'profile_candidate_registered','job_id':key[2],'content_sha256':digest,'publishing_attempted':False}
    finally:ledger.close()


def prepare(ledger,key,digest,media,binding,profile,client):
    doc=load(ledger,key,digest,media.sha256,binding)
    marker=p.read_state_marker(ledger.directory/'runtime.json');require_route(key[0],marker,media)
    old=ledger.get(key)
    if old and old['state'] in ('published','failed','unknown','confirming_label'):
        review.require(old['content_hash']==digest)
        return doc,None
    rows=review.collect(marker['accounts'],{key[0]:client});match_history(ledger,rows,doc,key,{key[0]:client})
    review.require(review.snapshot_digest(rows)==review.snapshot_digest(doc['history']))
    for row in rows:
        if (row['platform'],row['account_id'])==key[:2]:
            review.require(time.time()-row['published_at']>=profile['minimum_publish_interval_hours']*3600)
    return doc,rows


def claim(ledger,key,digest,media,binding,profile,context,upload_mode):
    review.require(upload_mode=='resumable' and key[0]=='instagram')
    ledger.db.execute('BEGIN IMMEDIATE')
    try:
        doc=load(ledger,key,digest,media.sha256,binding)
        review.require(context[0]==doc)
        old=ledger.get(key)
        if old:
            review.require(old['content_hash']==digest and old['auth_mode']=='unified' and old['upload_mode']=='resumable')
            ledger.db.execute('COMMIT');return old,False
        review.require(context[1] is not None)
        review.require(review.snapshot_digest(context[1])==review.snapshot_digest(doc['history']))
        # The unknown exclusions were checked under this same transaction, without changing them.
        exempt=absence.validate(ledger,doc['external_absences'],key) if doc['version']==2 else set()
        for row in ledger.db.execute("SELECT job_id,updated_at FROM jobs WHERE platform=? AND account_id=? AND state='published'",key[:2]):
            if (*key[:2],row['job_id']) not in exempt:
                review.require(row['updated_at'] is not None and time.time()-row['updated_at']>=profile['minimum_publish_interval_hours']*3600)
        source_key='profile_source:'+':'.join(key[:2])+':'+doc['source_sha256']
        ledger.db.execute('INSERT INTO recovery_metadata VALUES(?,?)',(source_key,key[2]))
        for sha in {doc['source_sha256'],media.sha256}:
            ledger.db.execute('INSERT INTO recovery_media VALUES(?,?,?)',(key[0],sha,key[2]))
        ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,content_hash,state,updated_at,auth_mode,upload_mode) VALUES(?,?,?,?,'claimed',?,'unified','resumable')",(*key,digest,time.time()))
        ledger.db.execute('COMMIT');return ledger.get(key),True
    except Exception:
        if ledger.db.in_transaction:ledger.db.execute('ROLLBACK')
        raise


def snapshot(directory,platform,job_id,external_absences=None):
    ledger=p.Ledger(directory)
    try:
        review.require(ledger.recovered and platform=='instagram')
        marker=p.read_state_marker(ledger.directory/'runtime.json')
        review.require(marker['accounts'][platform]['auth_mode']=='unified')
        key=(platform,marker['accounts'][platform]['account_id'],job_id)
        review.require(ledger.get(key) is None)
        rows=review.collect(marker['accounts'])
        if external_absences is None:review.match_ledger(ledger,rows)
        else:absence.match(ledger,rows,external_absences,key)
        pending=[{k:r[k] for k in ('platform','account_id','job_id','state')} for r in ledger.db.execute("SELECT * FROM jobs WHERE state NOT IN ('published','failed','duplicate')")]
        return {'runtime_id':marker['runtime_id'],'platform':platform,'account_id':key[1],'job_id':job_id,
                'history':rows,'prior_jobs_sha256':jobs_digest(ledger,key),'legacy_attempts_sha256':legacy_digest(ledger),
                'unresolved':pending,'publishing_attempted':False,'state_writes':0}
    finally:ledger.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',required=True)
    parser.add_argument('--snapshot',action='store_true')
    parser.add_argument('--platform',choices=['instagram'],default='instagram')
    parser.add_argument('--job-id')
    parser.add_argument('--external-absences',help='Private JSON object with external_absences for this exact new snapshot job.')
    for flag in ('review','source-file','video-file','text-file','publication-evidence','artifacts-dir'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    try:
        if args.snapshot:
            review.identifier(args.job_id)
            observations=None
            if args.external_absences:
                supplied=profiles.read_private(args.external_absences)
                review.keys(supplied,'external_absences');observations=supplied['external_absences']
            print(json.dumps(snapshot(args.state_dir,args.platform,args.job_id,observations)));return 0
        review.require(all((args.review,args.source_file,args.video_file,args.text_file,args.publication_evidence,args.artifacts_dir)))
        profile=profiles.configured();review.require(profile is not None)
        doc=profiles.read_private(args.review)
        media=p.prepare_video('instagram',args.video_file,ai_generated=profile['ai_disclosure'],allow_missing_url=True)
        text=p.validate_text('instagram',Path(args.text_file).read_text())
        print(json.dumps(register(args.state_dir,doc,media,text,args.source_file,args.publication_evidence,args.artifacts_dir)))
        return 0
    except Exception:
        print(json.dumps({'status':'blocked','error':'Profile recovery admission failed; existing receipts remain intact.'}));return 2

if __name__=='__main__':raise SystemExit(main())
