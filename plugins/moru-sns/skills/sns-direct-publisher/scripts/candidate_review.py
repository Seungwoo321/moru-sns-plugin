"""Candidate-scoped recovery review. All provider operations here are GETs."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace

import publisher as p
import private_runtime as runtime


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(ok):
    if not ok:
        raise p.SafeError('Candidate review is incomplete, mismatched, or already reserved.')


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value))


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value))


def keys(value, names):
    require(isinstance(value, dict) and set(value) == set(names.split()))


def file_digest(path):
    path = runtime.private_path(str(path))
    before = p.check_state_node(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    h = hashlib.sha256()
    with os.fdopen(fd, 'rb') as source:
        require(p.file_identity(os.fstat(source.fileno())) == p.file_identity(before))
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
        after = os.fstat(source.fileno())
    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    return h.hexdigest()


def receipt(platform, account, item):
    import runtime_guard as guard
    safe = guard.sanitized_posts([item], platform)[0]
    require(safe['permalink'] and safe['timestamp'])
    try:
        dt = p.datetime.fromisoformat(safe['timestamp'].replace('Z', '+00:00'))
        require(dt.tzinfo is not None)
        published = dt.timestamp()
        require(0 < published <= time.time() + 60)
    except (ValueError, TypeError, AttributeError):
        raise p.SafeError('Official receipt timestamp is invalid.') from None
    kind = item.get('media_type')
    text = item.get('text' if platform == 'threads' else 'caption', '')
    require(kind in ('TEXT_POST', 'IMAGE', 'VIDEO', 'CAROUSEL_ALBUM') and isinstance(text, str))
    return {'platform': platform, 'account_id': account, 'post_id': safe['id'],
            'published_at': published, 'permalink': safe['permalink'], 'media_type': kind,
            'text_sha256': digest(text)}


def owned(platform, binding, client):
    if platform == 'instagram' and binding.get('auth_mode') == 'unified':
        params, seen, identity = {'fields': 'id,username'}, set(), None
        for _ in range(10):
            result = p.api_dict(client.request('GET', '/me/assigned_instagram_accounts', params=params))
            accounts = result.get('data')
            require(isinstance(accounts, list))
            identity = next((p.api_dict(a) for a in accounts if p.api_dict(a).get('id') == binding['account_id']), None)
            if identity is not None:
                break
            cursor = p.api_dict(p.api_dict(result.get('paging', {})).get('cursors', {})).get('after')
            if not cursor:
                break
            require(isinstance(cursor, str) and re.fullmatch(r'[A-Za-z0-9_+/=-]{1,4096}', cursor) and cursor not in seen)
            seen.add(cursor); params = {'fields': 'id,username', 'after': cursor}
        require(identity is not None and identity.get('username') == binding['username'])
    else:
        id_field = 'user_id' if platform == 'instagram' else 'id'
        identity = p.api_dict(client.request('GET', '/me', params={'fields': id_field + ',username'}))
        if platform == 'instagram' and 'data' in identity:
            require(isinstance(identity['data'], list) and len(identity['data']) == 1)
            identity = p.api_dict(identity['data'][0])
        require(identity.get(id_field) == binding['account_id'] and identity.get('username') == binding['username'])
    edge = '/' + binding['account_id'] + ('/threads' if platform == 'threads' else '/media')
    params = {'fields': 'id,permalink,timestamp,media_type,' + ('text' if platform == 'threads' else 'caption'), 'limit': '25'}
    rows, seen = [], set()
    for _ in range(40):
        result = p.api_dict(client.request('GET', edge, params=params))
        data = result.get('data')
        require(isinstance(data, list))
        rows.extend(receipt(platform, binding['account_id'], item) for item in data)
        paging = p.api_dict(result.get('paging', {}))
        if not paging.get('next'):
            require(len({r['post_id'] for r in rows}) == len(rows))
            return rows
        cursor = p.api_dict(paging.get('cursors', {})).get('after')
        require(isinstance(cursor, str) and re.fullmatch(r'[A-Za-z0-9_+/=-]{1,4096}', cursor) and cursor not in seen)
        seen.add(cursor)
        params = {**params, 'after': cursor}
    raise p.SafeError('Full official history is required for candidate review.')


def collect(bindings, clients=None):
    rows = []
    require(set(bindings) <= {'threads', 'instagram'})
    for platform, binding in sorted(bindings.items()):
        client = (clients or {}).get(platform)
        if client is None:
            config = runtime.load_environment(platform)
            require(config['expected_user_id'] == binding['account_id'] and
                    config['expected_username'] == binding['username'] and
                    config['instagram_login_mode'] == binding['auth_mode'])
            client = p.APIClient(platform, config['access_token'], binding['auth_mode'])
        rows.extend(owned(platform, binding, client))
    return sorted(rows, key=lambda r: (r['platform'], r['account_id'], r['post_id']))


def snapshot_digest(rows):
    return digest(canonical(sorted(rows, key=lambda r: (r['platform'], r['account_id'], r['post_id']))))


def match_ledger(ledger, rows):
    actual = {(r['platform'], r['account_id'], r['post_id']): r for r in rows}
    require(len(actual) == len(rows))
    saved = {(r['platform'], r['account_id'], r['post_id']): dict(r)
             for r in ledger.db.execute("SELECT * FROM jobs WHERE state='published'")}
    require(set(actual) == set(saved))
    require(all(r['permalink'] == saved[key]['permalink'] for key, r in actual.items()))


def create_table(db):
    db.execute('''CREATE TABLE recovery_reviews(
        review_id TEXT PRIMARY KEY, platform TEXT NOT NULL, account_id TEXT NOT NULL,
        job_id TEXT NOT NULL, content_hash TEXT NOT NULL, media_sha256 TEXT NOT NULL,
        history_sha256 TEXT NOT NULL, document TEXT NOT NULL, document_sha256 TEXT NOT NULL,
        state TEXT NOT NULL, created_at REAL NOT NULL,
        UNIQUE(platform,account_id,job_id), UNIQUE(platform,account_id,media_sha256))''')


def validate_document(doc, marker):
    image_mode = doc.get('version') == 3
    keys(doc, 'version runtime_id review_id approval_record reviewer candidate history comparisons delivery' + (' fallback_approval_record fallback_report_sha256' if image_mode else ''))
    if image_mode:
        identifier(doc['fallback_approval_record']);sha(doc['fallback_report_sha256'])
    require(type(doc['version']) is int and doc['version'] in (2,3) and doc['runtime_id'] == marker['runtime_id'])
    for field in ('runtime_id', 'review_id', 'approval_record', 'reviewer'):
        identifier(doc[field])
    c = doc['candidate']
    keys(c, 'platform account_id job_id media_sha256 content_hash text_sha256 source_record ai_generated')
    require(c['platform'] in marker['accounts'] and c['account_id'] == marker['accounts'][c['platform']]['account_id'])
    require(type(c['ai_generated']) is bool)
    if image_mode:require(c['platform']=='threads' and c['ai_generated'] is False)
    identifier(c['job_id']); identifier(c['source_record'])
    for field in ('media_sha256', 'content_hash', 'text_sha256'):
        sha(c[field])
    if c['platform'] == 'instagram':
        require(doc['delivery'] is None)
    else:
        b = doc['delivery']
        keys(b, 'account_id post_id job_id source_sha256 url_sha256 remote_sha256 bytes inspection_sha256 expires_at' + (' kind width height' if image_mode else ''))
        if image_mode:
            require(b['kind']=='IMAGE' and type(b['width']) is int and type(b['height']) is int and b['bytes']<=8_000_000)
        identifier(b['account_id']); identifier(b['post_id']); identifier(b['job_id'])
        for field in ('source_sha256','url_sha256','remote_sha256','inspection_sha256'):
            sha(b[field])
        require(b['remote_sha256'] == c['media_sha256'])
        require(type(b['bytes']) is int and 0 < b['bytes'] <= 300_000_000)
        require(type(b['expires_at']) in (int,float) and time.time() < b['expires_at'] <= time.time()+1800)
    require(isinstance(doc['history'], list) and len(doc['history']) <= 2000)
    require(isinstance(doc['comparisons'], list) and len(doc['comparisons']) == len(doc['history']))
    seen = set()
    for item in doc['comparisons']:
        keys(item, 'platform account_id post_id source_kind artifact_sha256 receipt_source_record comparison_record report_sha256 complete verdict paired_job_id')
        key = item['platform'], item['account_id'], item['post_id']
        require(key not in seen); seen.add(key)
        require(item['complete'] is True)
        require(item['source_kind'] in ('official_public_reencoding', 'official_public_thumbnail', 'verified_original', 'official_text'))
        require(item['verdict'] in ('distinct', 'authorized_cross_platform'))
        identifier(item['receipt_source_record']); identifier(item['comparison_record'])
        sha(item['artifact_sha256']); sha(item['report_sha256'])
        if item['verdict'] == 'distinct':
            require(item['paired_job_id'] is None)
        else:
            identifier(item['paired_job_id'])
    require(seen == {(r['platform'], r['account_id'], r['post_id']) for r in doc['history']})


def register(directory, doc, media_path, text_path, artifacts_dir, *, collector=collect, pair_id=None, image_mode=False):
    """Trusted operator imports a complete material review; never publishes."""
    marker = p.read_state_marker(p.state_path(directory) / 'runtime.json')
    require('recovery' in marker)
    validate_document(doc, marker)
    require(image_mode == (doc['version']==3) and (not image_mode or pair_id is not None))
    c = doc['candidate']
    require(file_digest(media_path) == c['media_sha256'])
    require(file_digest(text_path) == c['text_sha256'])
    text = Path(text_path).read_text(encoding='utf-8')
    require(digest(text) == c['text_sha256'])
    require(p.validate_text(c['platform'],text) == text)
    video = SimpleNamespace(sha256=c['media_sha256'], ai_generated=c['ai_generated'],media_type='IMAGE' if image_mode else 'VIDEO')
    require(p.content_hash(text, video) == c['content_hash'])
    artifact_root = runtime.private_path(str(artifacts_dir))
    p.check_state_node(artifact_root, directory=True)
    if image_mode:require(file_digest(artifact_root/doc['fallback_report_sha256'])==doc['fallback_report_sha256'])
    for item in doc['comparisons']:
        for field in ('artifact_sha256', 'report_sha256'):
            require(file_digest(artifact_root / item[field]) == item[field])
    ledger = p.Ledger(directory)
    try:
        require(ledger.recovered)
        provenance = json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])
        if pair_id is None:
            require(provenance['inventory_complete'] and provenance['old_writers_stopped'])
            require(not ledger.db.execute("SELECT 1 FROM jobs WHERE state NOT IN ('published','failed','duplicate') LIMIT 1").fetchone())
        rows = collector(marker['accounts'])
        require(snapshot_digest(rows) == snapshot_digest(doc['history']))
        match_ledger(ledger, rows)
        if pair_id is not None:
            import candidate_pair
            candidate_pair.check(ledger,pair_id,doc,rows,allow_image_registration=image_mode)
        by_key = {(r['platform'], r['account_id'], r['post_id']): r for r in rows}
        for item in doc['comparisons']:
            past = by_key[item['platform'], item['account_id'], item['post_id']]
            require((past['media_type'] == 'TEXT_POST') == (item['source_kind'] == 'official_text'))
            if item['verdict'] == 'distinct':
                require(item['artifact_sha256'] != c['media_sha256'])
            else:
                require(item['platform'] != c['platform'])
                previous = ledger.get((item['platform'], item['account_id'], item['paired_job_id']))
                require(previous and previous['state'] == 'published' and previous['post_id'] == item['post_id'])
                claimed = ledger.db.execute('SELECT job_id FROM recovery_media WHERE platform=? AND sha256=?',
                                            (item['platform'], doc['delivery']['source_sha256'] if doc['delivery'] else c['media_sha256'])).fetchone()
                require(claimed and claimed[0] == item['paired_job_id'])
        # A reported distinct comparison cannot override a known exact prior source.
        for platform, job in ledger.db.execute('SELECT platform,job_id FROM recovery_media WHERE sha256=?', (c['media_sha256'],)):
            require(any(i['platform'] == platform and i['paired_job_id'] == job and i['verdict'] == 'authorized_cross_platform'
                        for i in doc['comparisons']))
        key = c['platform'], c['account_id'], c['job_id']
        require(ledger.get(key) is None)
        require(not ledger.db.execute('SELECT 1 FROM jobs WHERE platform=? AND account_id=? AND content_hash=?',
                                      (*key[:2], c['content_hash'])).fetchone())
        import delivery_binding
        delivery_binding.verify(ledger, doc, artifacts_dir=artifact_root, media_path=media_path)
        document = canonical(doc)
        ledger.db.execute('BEGIN IMMEDIATE')
        ledger.db.execute('INSERT INTO recovery_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (doc['review_id'], *key, c['content_hash'], c['media_sha256'], snapshot_digest(rows),
                           document, digest(document), 'approved', time.time()))
        if image_mode:
            ledger.db.execute('INSERT INTO recovery_metadata VALUES(?,?)',('pair_image:'+pair_id,canonical({'document_sha256':digest(document)})))
        if pair_id is not None:
            ledger.db.execute('INSERT INTO pair_review_bindings VALUES(?,?)',(doc['review_id'],pair_id))
        ledger.db.execute('COMMIT')
        return {'status': 'candidate_review_registered', 'review_id': doc['review_id'],
                'history_count': len(rows), 'publishing_attempted': False}
    finally:
        ledger.close()


def authorize(ledger, key, content_hash, media_sha256, history_digest, old):
    row = ledger.db.execute('SELECT * FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?', key).fetchone()
    require(row and row['content_hash'] == content_hash and row['media_sha256'] == media_sha256)
    require(row['history_sha256'] == history_digest and digest(row['document']) == row['document_sha256'])
    require((row['state'] == 'approved' and old is None) or
            (row['state'] == 'consumed' and old and old['state'] in ('media_processing', 'published')))
    return row['review_id']
