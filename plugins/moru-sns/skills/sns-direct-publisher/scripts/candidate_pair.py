"""Explicit one-pair admission, separate from global recovery completeness."""
import json
import re
import subprocess
import time

import publisher as p
import candidate_review as review


def create_tables(db):
    db.execute('CREATE TABLE candidate_pair(singleton INTEGER PRIMARY KEY CHECK(singleton=1), pair_id TEXT UNIQUE, document TEXT, digest TEXT, legacy TEXT, created_at REAL)')
    db.execute('CREATE TABLE pair_review_bindings(review_id TEXT PRIMARY KEY,pair_id TEXT NOT NULL)')
    db.execute('CREATE TABLE recovery_legacy_attempts(platform TEXT,job_id TEXT,document TEXT,PRIMARY KEY(platform,job_id))')


def unresolved(ledger):
    return [dict(row) for row in ledger.db.execute("SELECT * FROM jobs WHERE state NOT IN ('published','failed','duplicate') ORDER BY platform,account_id,job_id")]


def key(row):
    return row['platform'], row['account_id'], row['job_id']


def load(ledger, pair_id):
    row = ledger.db.execute('SELECT * FROM candidate_pair WHERE singleton=1 AND pair_id=?', (pair_id,)).fetchone()
    review.require(row and review.digest(row['document']+'\n'+row['legacy']) == row['digest'])
    doc = json.loads(row['document'])
    review.require(doc['runtime_id'] == p.read_state_marker(ledger.directory/'runtime.json')['runtime_id'])
    return doc, json.loads(row['legacy'])


def derivation(ledger, pair_id):
    row = ledger.db.execute('SELECT value FROM recovery_metadata WHERE key=?',('pair_derivation:'+pair_id,)).fetchone()
    if row is None:
        return None
    envelope = json.loads(row[0])
    review.require(review.digest(review.canonical(envelope['document'])) == envelope['sha256'])
    return envelope['document']


def video_counts(path):
    try:
        result = subprocess.run(['ffprobe','-v','error','-count_frames','-show_entries',
                                 'stream=codec_type,nb_read_frames','-of','json',str(path)],
                                capture_output=True,check=True,timeout=120)
        streams = json.loads(result.stdout)['streams']
        video = [s for s in streams if s['codec_type']=='video']
        review.require(len(video)==1)
        return int(video[0]['nb_read_frames']),sum(s['codec_type']=='audio' for s in streams)
    except Exception:
        raise p.SafeError('Local derivation frame/audio inspection failed.') from None


def register_derivation(directory, doc, source_path, derived_path, artifacts_dir):
    """Append one private mapping before any review/claim; never rewrite the pair."""
    review.keys(doc,'version runtime_id pair_id account_id job_id source_sha256 derived_sha256 frame_count frames_preserved audio_preserved transform_kind approval_record inspection_sha256')
    review.require(type(doc['version']) is int and doc['version']==1)
    review.require(doc['transform_kind']=='format_only' and doc['frames_preserved'] is True and doc['audio_preserved'] is True)
    review.require(type(doc['frame_count']) is int and doc['frame_count']>0)
    for field in ('runtime_id','pair_id','account_id','job_id','approval_record'):
        review.identifier(doc[field])
    for field in ('source_sha256','derived_sha256','inspection_sha256'):
        review.sha(doc[field])
    review.require(doc['source_sha256'] != doc['derived_sha256'])
    review.require(review.file_digest(source_path)==doc['source_sha256'])
    review.require(review.file_digest(derived_path)==doc['derived_sha256'])
    review.require(review.file_digest(artifacts_dir/doc['inspection_sha256'])==doc['inspection_sha256'])
    source_counts, derived_counts = video_counts(source_path),video_counts(derived_path)
    review.require(source_counts==derived_counts and source_counts[0]==doc['frame_count'] and source_counts[1]>0)
    video = p.prepare_video('instagram',str(derived_path),allow_missing_url=True)
    review.require(video.preflight=='verified_local_profile' and video.sha256==doc['derived_sha256'])
    # Recheck bytes after inspecting; the inspected files must be the registered files.
    review.require(review.file_digest(source_path)==doc['source_sha256'] and review.file_digest(derived_path)==doc['derived_sha256'])
    ledger = p.Ledger(directory)
    try:
        manifest, legacy = load(ledger,doc['pair_id'])
        review.require(doc['runtime_id']==manifest['runtime_id'] and doc['source_sha256']==manifest['source']['sha256'])
        ig = next(c for c in manifest['candidates'] if c['platform']=='instagram')
        review.require(doc['account_id']==ig['account_id'] and doc['job_id']==ig['job_id'])
        review.require(unresolved(ledger)==legacy)
        for c in manifest['candidates']:
            review.require(ledger.get(key(c)) is None)
            review.require(not ledger.db.execute('SELECT 1 FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?',key(c)).fetchone())
        review.require(not ledger.db.execute('SELECT 1 FROM recovery_media WHERE sha256 IN (?,?)',(doc['source_sha256'],doc['derived_sha256'])).fetchone())
        for (raw,) in ledger.db.execute('SELECT document FROM recovery_legacy_attempts'):
            review.require(json.loads(raw).get('media_sha256') not in (doc['source_sha256'],doc['derived_sha256']))
        envelope = review.canonical({'document':doc,'sha256':review.digest(review.canonical(doc))})
        ledger.db.execute('BEGIN IMMEDIATE')
        ledger.db.execute('INSERT INTO recovery_metadata VALUES(?,?)',('pair_derivation:'+doc['pair_id'],envelope))
        ledger.db.execute('COMMIT')
        return {'status':'pair_derivation_registered','pair_id':doc['pair_id'],'publishing_attempted':False,'pair_source_unchanged':True}
    finally:
        ledger.close()


def validate(doc, marker):
    review.keys(doc, 'version runtime_id pair_id approval_record source candidates history candidate_never_attempted single_writer_confirmed evidence_sha256 legacy_unresolved')
    review.require(type(doc['version']) is int and doc['version'] == 1)
    review.require(doc['runtime_id'] == marker['runtime_id'])
    for field in ('runtime_id','pair_id','approval_record'):
        review.identifier(doc[field])
    review.require(doc['candidate_never_attempted'] is True and doc['single_writer_confirmed'] is True)
    review.sha(doc['evidence_sha256'])
    source = doc['source']
    review.keys(source, 'slack_channel slack_message_ts slack_file_id sha256')
    review.require(source['slack_channel'] in ('D0CDWG6PE4U','D0CJGTZ9BN3'))
    review.require(isinstance(source['slack_message_ts'],str) and re.fullmatch(r'[0-9]+\.[0-9]{6}',source['slack_message_ts']))
    review.require(isinstance(source['slack_file_id'],str) and re.fullmatch(r'F[A-Z0-9]+',source['slack_file_id']))
    review.sha(source['sha256'])
    review.require(isinstance(doc['candidates'],list) and len(doc['candidates']) == 2)
    review.require({c['platform'] for c in doc['candidates']} == {'threads','instagram'})
    for c in doc['candidates']:
        review.keys(c,'platform account_id job_id ordinal text_sha256')
        review.require(c['account_id'] == marker['accounts'][c['platform']]['account_id'])
        review.identifier(c['job_id']); review.sha(c['text_sha256'])
        review.require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',c['job_id']))
        review.require(type(c['ordinal']) is int and c['ordinal'] == (3 if c['platform']=='threads' else 2))
    review.require(isinstance(doc['history'],list) and len(doc['history']) == 3)
    review.require(isinstance(doc['legacy_unresolved'],list))
    for item in doc['legacy_unresolved']:
        review.keys(item,'platform account_id job_id exclusion_evidence_sha256')
        review.sha(item['exclusion_evidence_sha256'])


def register(directory, doc, media_path, text_paths, artifacts_dir, *, collector=review.collect):
    ledger = p.Ledger(directory)
    try:
        review.require(ledger.recovered)
        marker = p.read_state_marker(ledger.directory/'runtime.json')
        validate(doc,marker)
        provenance = json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])
        # A pair has no authority to assert retirement of old writers on its own.
        review.require(provenance['old_writers_stopped'] and provenance['inventory_complete'] is False)
        review.require(review.file_digest(media_path) == doc['source']['sha256'])
        review.require(review.file_digest(artifacts_dir/doc['evidence_sha256']) == doc['evidence_sha256'])
        for c in doc['candidates']:
            review.require(review.file_digest(text_paths[c['platform']]) == c['text_sha256'])
            from pathlib import Path
            text = Path(text_paths[c['platform']]).read_text(encoding='utf-8')
            review.require(p.validate_text(c['platform'],text) == text)
            review.require(ledger.get(key(c)) is None)
        rows = collector(marker['accounts'])
        review.require(review.snapshot_digest(rows) == review.snapshot_digest(doc['history']))
        review.match_ledger(ledger,rows)
        for c in doc['candidates']:
            review.require(sum((r['platform'],r['account_id']) == key(c)[:2] for r in rows) == c['ordinal']-1)
        old = unresolved(ledger)
        declared = {key(i):i for i in doc['legacy_unresolved']}
        review.require(len(declared) == len(doc['legacy_unresolved']) and set(declared) == {key(i) for i in old})
        for item in old:
            h = declared[key(item)]['exclusion_evidence_sha256']
            review.require(review.file_digest(artifacts_dir/h) == h)
        # These claimed hashes are not promoted to verified receipts, but cannot be ignored.
        for (raw,) in ledger.db.execute('SELECT document FROM recovery_legacy_attempts'):
            attempt = json.loads(raw)
            review.require(attempt.get('media_sha256') != doc['source']['sha256'])
        review.require(not ledger.db.execute('SELECT 1 FROM recovery_media WHERE sha256=?',(doc['source']['sha256'],)).fetchone())
        document = review.canonical(doc)
        legacy = review.canonical(old)
        ledger.db.execute('BEGIN IMMEDIATE')
        ledger.db.execute('INSERT INTO candidate_pair VALUES(1,?,?,?,?,?)',
                          (doc['pair_id'],document,review.digest(document+'\n'+legacy),legacy,time.time()))
        ledger.db.execute('COMMIT')
        return {'status':'one_pair_registered','pair_id':doc['pair_id'],'global_inventory_changed':False,'publishing_attempted':False}
    finally:
        ledger.close()


def check(ledger,pair_id,candidate_doc,rows):
    """Exact live admission; preserves legacy rows, admits only the named pair."""
    doc, legacy = load(ledger,pair_id)
    c = candidate_doc['candidate']
    review.require(c['ai_generated'] is False)
    target = next((item for item in doc['candidates'] if key(item) == key(c)),None)
    review.require(target is not None and c['text_sha256'] == target['text_sha256'])
    original = doc['source']['sha256']
    mapping = derivation(ledger,pair_id)
    upload_sha = original
    if mapping is not None:
        ig = next(item for item in doc['candidates'] if item['platform']=='instagram')
        review.require(mapping['source_sha256']==original and mapping['runtime_id']==doc['runtime_id'] and
                       mapping['pair_id']==pair_id and mapping['account_id']==ig['account_id'] and mapping['job_id']==ig['job_id'])
        upload_sha = mapping['derived_sha256']
    if c['platform'] == 'instagram':
        review.require(c['media_sha256'] == upload_sha and candidate_doc['delivery'] is None)
    else:
        review.require(candidate_doc['delivery']['source_sha256'] == upload_sha)
    provenance = json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])
    review.require(provenance['old_writers_stopped'] and provenance['inventory_complete'] is False)
    legacy_map = {key(item):item for item in legacy}
    pair_keys = {key(item) for item in doc['candidates']}
    # Even a declared legacy exclusion cannot hide a matching candidate digest.
    review.require(not any(item.get('content_hash') == c['content_hash'] for item in legacy))
    for (raw,) in ledger.db.execute('SELECT document FROM recovery_legacy_attempts'):
        attempt = json.loads(raw)
        review.require(attempt.get('content_hash') != c['content_hash'] and attempt.get('media_sha256') not in (original,upload_sha,c['media_sha256']))
    actual_legacy = {key(item):item for item in unresolved(ledger) if key(item) not in pair_keys}
    review.require(actual_legacy == legacy_map)
    for member in doc['candidates']:
        saved = ledger.get(key(member))
        if saved is not None:
            review.require(saved['state'] == 'published' or (key(member) == key(c) and saved['state'] == 'media_processing'))
    baseline = {(r['platform'],r['account_id'],r['post_id']):r for r in doc['history']}
    actual = {(r['platform'],r['account_id'],r['post_id']):r for r in rows}
    review.require(len(actual) == len(rows) and all(actual.get(k) == v for k,v in baseline.items()))
    extra = {k:v for k,v in actual.items() if k not in baseline}
    if c['platform'] == 'instagram':
        review.require(not extra)
    else:
        ig = next(item for item in doc['candidates'] if item['platform'] == 'instagram')
        saved = ledger.get(key(ig))
        review.require(saved and saved['state'] == 'published' and saved['permalink'])
        receipt_key = ('instagram',ig['account_id'],saved['post_id'])
        review.require(set(extra) == {receipt_key})
        receipt = extra[receipt_key]
        review.require(receipt['permalink'] == saved['permalink'] and receipt['media_type'] == 'VIDEO' and receipt['text_sha256'] == ig['text_sha256'])
        review.require(candidate_doc['delivery']['job_id'] == ig['job_id'] and candidate_doc['delivery']['post_id'] == saved['post_id'])
    review.require(sum((r['platform'],r['account_id']) == key(c)[:2] for r in rows) == target['ordinal']-1)
    return doc
