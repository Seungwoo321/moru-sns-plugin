"""Guarded v1 recovery migration and GET-only candidate review registration."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/moru-sns/skills/sns-direct-publisher/scripts'))
import publisher as p
import private_runtime as runtime
import runtime_guard as guard
import candidate_review as review
import candidate_pair


class Blocked(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise Blocked('invalid_identifier')
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        seconds = result.timestamp()
        if seconds <= 0 or seconds > time.time() + 60:
            raise ValueError()
        return seconds
    except (ValueError, TypeError, AttributeError):
        raise Blocked('invalid_timestamp') from None


def validate_manifest(m):
    if set(m) != {'version', 'source_runtime', 'source_record', 'inventory_complete', 'old_writers_stopped', 'attempts'} or m['version'] != 1:
        raise Blocked('invalid_manifest')
    identifier(m['source_runtime']); identifier(m['source_record'])
    if type(m['inventory_complete']) is not bool or type(m['old_writers_stopped']) is not bool:
        raise Blocked('explicit_attestations_required')
    if not isinstance(m['attempts'], list) or len(m['attempts']) > 10000:
        raise Blocked('invalid_attempts')
    seen = set()
    for a in m['attempts']:
        if set(a) != {'platform', 'job_id', 'post_id', 'content_hash', 'media_sha256'} or a['platform'] not in ('threads', 'instagram'):
            raise Blocked('invalid_attempt')
        identifier(a['job_id'])
        key = a['platform'], a['job_id']
        if key in seen:
            raise Blocked('duplicate_job')
        seen.add(key)
        if a['post_id'] is not None and not re.fullmatch(r'[0-9]{1,40}', a['post_id']):
            raise Blocked('invalid_post_id')
        for field in ('content_hash', 'media_sha256'):
            if a[field] is not None and not re.fullmatch(r'[a-f0-9]{64}', a[field]):
                raise Blocked('invalid_hash')
    return m


def fetch_owned(platform, config):
    """Read all owned-media pages; never follow a returned URL or persist media_url."""
    runtime.verify_identity(platform, config['access_token'], config['expected_username'],
                            config['expected_user_id'], config['instagram_login_mode'])
    client = p.APIClient(platform, config['access_token'], config['instagram_login_mode'])
    fields = 'id,permalink,timestamp,media_type,' + ('text' if platform == 'threads' else 'caption')
    edge = '/' + config['expected_user_id'] + ('/threads' if platform == 'threads' else '/media')
    params, rows, cursors = {'fields': fields, 'limit': '25'}, [], set()
    for _ in range(40):
        result = p.api_dict(client.request('GET', edge, params=params))
        raw = result.get('data')
        safe = guard.sanitized_posts(raw, platform)
        for item, post in zip(raw, safe):
            if not post['permalink'] or not post['timestamp']:
                raise Blocked('incomplete_official_receipt')
            timestamp(post['timestamp'])
            kind = item.get('media_type')
            text = item.get('text' if platform == 'threads' else 'caption', '')
            if kind not in ('TEXT_POST', 'IMAGE', 'VIDEO', 'CAROUSEL_ALBUM') or not isinstance(text, str):
                raise Blocked('invalid_official_media')
            rows.append({**post, 'platform': platform, 'account_id': config['expected_user_id'],
                         'media_type': kind, 'text_sha256': digest(text),
                         'content_hash': p.content_hash(text) if kind == 'TEXT_POST' else None})
        paging = result.get('paging', {})
        if not paging.get('next'):
            if len({r['id'] for r in rows}) != len(rows):
                raise Blocked('duplicate_official_id')
            return rows
        after = paging.get('cursors', {}).get('after')
        if not isinstance(after, str) or not re.fullmatch(r'[A-Za-z0-9_+/=-]{1,4096}', after) or after in cursors:
            raise Blocked('unverified_pagination')
        cursors.add(after); params = {**params, 'after': after}
    raise Blocked('history_truncated')


@contextmanager
def parent_lock(path):
    path = runtime.private_path(str(path))
    fd = runtime.parent_fd(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path
    finally:
        os.close(fd)


def initialize(destination, manifest, configs, fetch=fetch_owned):
    """Explicit new evidence store; fail closed on interruption, no resume/overwrite."""
    validate_manifest(manifest)
    if not configs or set(configs) - {'threads', 'instagram'}:
        raise Blocked('invalid_connections')
    if any(a['platform'] not in configs for a in manifest['attempts']):
        raise Blocked('unconfigured_attempt')
    with parent_lock(destination) as path:
        if path.exists():
            raise Blocked('destination_exists')
        # No local state created unless every selected account's full query succeeds.
        rows = [r for platform, config in configs.items() for r in fetch(platform, config)]
        bindings = {k: {'account_id': v['expected_user_id'], 'username': v['expected_username'],
                        'auth_mode': v['instagram_login_mode']} for k, v in configs.items()}
        if any(r['platform'] not in bindings or r['account_id'] != bindings[r['platform']]['account_id'] for r in rows):
            raise Blocked('account_mismatch')
        os.mkdir(path, 0o700)
        dbpath = path / 'recovery.sqlite3'
        fd = os.open(dbpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600); os.close(fd)
        db = sqlite3.connect(dbpath)
        try:
            db.executescript('''
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE posts(platform TEXT, account_id TEXT, post_id TEXT,
                    published_at REAL NOT NULL, permalink TEXT NOT NULL, media_type TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL, content_hash TEXT, PRIMARY KEY(platform, post_id));
                CREATE TABLE attempts(platform TEXT, job_id TEXT, post_id TEXT, content_hash TEXT,
                    media_sha256 TEXT, state TEXT NOT NULL, PRIMARY KEY(platform,job_id));
                CREATE TABLE claims(platform TEXT, job_id TEXT, content_hash TEXT, media_sha256 TEXT,
                    state TEXT NOT NULL, PRIMARY KEY(platform,job_id), UNIQUE(platform,content_hash),
                    UNIQUE(platform,media_sha256));
            ''')
            db.execute('BEGIN IMMEDIATE')
            meta = {'schema': 'moru-v1-recovery-evidence-1', 'manifest': manifest,
                    'manifest_sha256': digest(canonical(manifest)), 'accounts': bindings,
                    'queried_at': time.time(), 'publisher_compatible': False,
                    'original_ledger_modified': False}
            db.executemany('INSERT INTO metadata VALUES(?,?)', [(k, canonical(v)) for k,v in meta.items()])
            for r in rows:
                db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?,?)', (r['platform'],r['account_id'],r['id'],
                    timestamp(r['timestamp']),r['permalink'],r['media_type'],r['text_sha256'],r.get('content_hash')))
            owned = {(r['platform'],r['id']) for r in rows}
            for a in manifest['attempts']:
                state = 'confirmed_post_reference' if (a['platform'],a['post_id']) in owned else 'quarantined'
                db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)', (a['platform'],a['job_id'],a['post_id'],
                    a['content_hash'],a['media_sha256'],state))
            db.execute("INSERT INTO metadata VALUES('complete','true')")
            db.commit()
        finally:
            db.close()
        for target in (dbpath,path,path.parent):
            fd=os.open(target,os.O_RDONLY | os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)
    return {'status':'recovery_evidence_initialized','official_posts':len(rows),'publish_ready':False}


def inspect(destination):
    path = runtime.private_path(str(destination)); p.check_state_node(path,directory=True)
    database = path/'recovery.sqlite3'; p.check_state_node(database)
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]: raise Blocked('integrity_failure')
        meta={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}
        if meta.get('complete') is not True: raise Blocked('incomplete_recovery')
        if digest(canonical(meta['manifest'])) != meta['manifest_sha256']: raise Blocked('provenance_mismatch')
        reasons=['native_migration_required']
        if not meta['manifest']['old_writers_stopped']: reasons.append('old_writers_not_stopped')
        if not meta['manifest']['inventory_complete']: reasons.append('attempt_inventory_incomplete')
        if db.execute("SELECT 1 FROM attempts WHERE state='quarantined' LIMIT 1").fetchone(): reasons.append('unresolved_attempts')
        # Graph rendition URLs/hashes do not establish original source-byte identity.
        if db.execute("SELECT 1 FROM posts WHERE media_type!='TEXT_POST' LIMIT 1").fetchone(): reasons.append('original_media_identity_unverified')
        return {'status':'review_required','publish_ready':False,'reasons':reasons,
                'official_posts':db.execute('SELECT COUNT(*) FROM posts').fetchone()[0]}


def reserve(destination, platform, job_id, content_hash, media_sha256):
    """Reserved for reviewed integration. Deliberately no way to activate a writer."""
    report=inspect(destination)
    if report['reasons']: raise Blocked('recovery_not_publishable')
    raise Blocked('native_migration_required')


def migrate(evidence_dir, destination, runtime_id):
    """Produce a native v1 runtime, with marker written only after imported state commits."""
    identifier(runtime_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",runtime_id):
        raise Blocked('invalid_runtime_id')
    inspect(evidence_dir)
    evidence=runtime.private_path(str(evidence_dir))/'recovery.sqlite3'
    with sqlite3.connect(evidence.as_uri()+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row
        source.execute('BEGIN')
        meta={k:json.loads(v) for k,v in source.execute('SELECT key,value FROM metadata')}
        posts=[dict(row) for row in source.execute('SELECT * FROM posts')]
        attempts=[dict(row) for row in source.execute('SELECT * FROM attempts')]
    manifest=meta['manifest']
    provenance=canonical({'version':1,'source_runtime':manifest['source_runtime'],
        'source_record':manifest['source_record'],'manifest_sha256':meta['manifest_sha256'],
        'inventory_complete':manifest['inventory_complete'],'old_writers_stopped':manifest['old_writers_stopped'],
        'queried_at':meta['queried_at'],'evidence_sha256':digest(canonical({'posts':posts,'attempts':attempts})),
        'imported_at':time.time()})
    def populate(ledger):
        db=ledger.db
        db.executescript("""
            CREATE TABLE recovery_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE recovery_posts(platform TEXT,post_id TEXT,media_type TEXT,
                content_hash TEXT,media_sha256 TEXT,PRIMARY KEY(platform,post_id));
            CREATE TABLE recovery_media(platform TEXT,sha256 TEXT,job_id TEXT,PRIMARY KEY(platform,sha256));
        """)
        review.create_table(db)
        candidate_pair.create_tables(db)
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('INSERT INTO recovery_metadata VALUES(?,?)',('provenance',provenance))
            for a in attempts:
                db.execute('INSERT INTO recovery_legacy_attempts VALUES(?,?,?)',(a['platform'],a['job_id'],canonical(a)))
            used=set()
            for post in posts:
                matches=[a for a in attempts if a['platform']==post['platform'] and a['post_id']==post['post_id']]
                if len(matches)>1:raise Blocked('ambiguous_job_mapping')
                job=matches[0]['job_id'] if matches else 'imported.'+post['post_id']
                if (post['platform'],job) in used:raise Blocked('duplicate_job_mapping')
                used.add((post['platform'],job))
                # Only text digests derived from official text can be restored automatically.
                # Caller-provided media/content hashes are evidence, not verified source identity.
                content=post.get('content_hash') if post['media_type']=='TEXT_POST' else None
                binding=meta['accounts'][post['platform']]
                db.execute('INSERT INTO jobs(platform,account_id,job_id,content_hash,state,post_id,permalink,updated_at,auth_mode) VALUES(?,?,?,?,?,?,?,?,?)',
                    (post['platform'],post['account_id'],job,content,'published',post['post_id'],post['permalink'],post['published_at'],binding['auth_mode']))
                db.execute('INSERT INTO recovery_posts VALUES(?,?,?,?,?)',(post['platform'],post['post_id'],post['media_type'],content,None))
            for a in attempts:
                if a['state']=='confirmed_post_reference':continue
                if (a['platform'],a['job_id']) in used:raise Blocked('conflicting_attempt')
                binding=meta['accounts'][a['platform']]
                db.execute('INSERT INTO jobs(platform,account_id,job_id,content_hash,state,updated_at,auth_mode) VALUES(?,?,?,?,?,?,?)',
                    (a['platform'],binding['account_id'],a['job_id'],a['content_hash'],'unknown',time.time(),binding['auth_mode']))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK');raise
    with parent_lock(destination) as path:
        guard.initialize_state(path,runtime_id,meta['accounts'],populate=populate,
            recovery={'version':1,'sha256':digest(provenance)})
        count=p.inspect_state(path,runtime_id,meta['accounts'])
    return {'status':'migrated','native_v1_guard':'passed','jobs':count,
            'publication_requires_history_gate':True,'publishing_attempted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['initialize','inspect','migrate','review-snapshot','approve-candidate','register-pair'])
    parser.add_argument('--state-dir',required=True)
    parser.add_argument('--manifest')
    parser.add_argument('--review')
    parser.add_argument('--pair-manifest')
    parser.add_argument('--pair-id')
    parser.add_argument('--instagram-text-file')
    parser.add_argument('--threads-text-file')
    parser.add_argument('--media-file')
    parser.add_argument('--text-file')
    parser.add_argument('--artifacts-dir')
    parser.add_argument('--evidence-dir')
    parser.add_argument('--runtime-id')
    parser.add_argument('--connection',choices=['threads','instagram'],action='append')
    args=parser.parse_args()
    try:
        if args.operation=='register-pair':
            source=runtime.private_path(args.pair_manifest);p.check_state_node(source)
            if source.stat().st_size>2_000_000:raise Blocked('pair_manifest_too_large')
            result=candidate_pair.register(args.state_dir,json.loads(source.read_text()),args.media_file,
                {'instagram':args.instagram_text_file,'threads':args.threads_text_file},runtime.private_path(args.artifacts_dir))
        elif args.operation=='review-snapshot':
            ledger=p.Ledger(args.state_dir)
            try:
                marker=p.read_state_marker(p.state_path(args.state_dir)/'runtime.json')
                rows=review.collect(marker['accounts'])
                review.match_ledger(ledger,rows)
                result={'history':rows,'history_sha256':review.snapshot_digest(rows),'publishing_attempted':False}
            finally:ledger.close()
        elif args.operation=='approve-candidate':
            source=runtime.private_path(args.review);p.check_state_node(source)
            if source.stat().st_size>2_000_000:raise Blocked('review_too_large')
            result=review.register(args.state_dir,json.loads(source.read_text()),args.media_file,args.text_file,args.artifacts_dir,pair_id=args.pair_id)
        elif args.operation=='inspect': result=inspect(args.state_dir)
        elif args.operation=='migrate':
            result=migrate(args.evidence_dir,args.state_dir,args.runtime_id)
        else:
            if not args.manifest or not args.connection or len(set(args.connection))!=len(args.connection):
                raise Blocked('explicit_manifest_connections_required')
            source=runtime.private_path(args.manifest);p.check_state_node(source)
            if source.stat().st_size>2_000_000:raise Blocked('manifest_too_large')
            manifest=json.loads(source.read_text())
            configs={k:runtime.load_environment(k) for k in args.connection}
            result=initialize(args.state_dir,manifest,configs)
        print(json.dumps(result));return 0
    except Exception:
        print(json.dumps({'status':'blocked','publish_ready':False,'error':'Recovery failed; do not overwrite or publish.'}));return 2

if __name__=='__main__':raise SystemExit(main())
