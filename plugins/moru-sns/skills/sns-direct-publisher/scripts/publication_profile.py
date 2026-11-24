"""Read-only private operator profile and exact candidate review binding."""
import hashlib
import json
import math
import os
from pathlib import Path
import time


def fail(message):
    import publisher as p
    raise p.SafeError(message)


def read_private(path):
    import publisher as p
    path = p.state_path(str(Path(path).expanduser()))
    p.check_state_node(path.parent, directory=True)
    info = p.check_state_node(path)
    if info.st_size > 65536:
        fail('Publication profile/evidence is too large.')
    with path.open('r', encoding='utf-8') as source:
        return json.load(source)


def configured():
    path = os.environ.get('MORU_PUBLICATION_PROFILE')
    if not path:
        return None
    doc = read_private(path)
    if (set(doc) != {'version','profile_id','language','require_fresh_capture','max_capture_age_hours','ai_disclosure','check_interval_hours','minimum_publish_interval_hours'} or
            doc['version'] != 1 or not isinstance(doc['profile_id'],str) or not doc['profile_id'] or
            doc['language'] != 'en' or type(doc['require_fresh_capture']) is not bool or
            type(doc['ai_disclosure']) is not bool or type(doc['max_capture_age_hours']) is not int or
            not 1 <= doc['max_capture_age_hours'] <= 168 or
            type(doc['check_interval_hours']) is not int or doc['check_interval_hours'] < 1 or
            type(doc['minimum_publish_interval_hours']) is not int or doc['minimum_publish_interval_hours'] < 6):
        fail('Unsupported or incomplete publication profile.')
    return doc


def bind(profile, evidence_path, source_path, platform, text, media, *, confirming=False):
    import publisher as p
    import media_policy
    if not evidence_path or not source_path:
        fail('Configured publication profile requires source file and private language/capture review evidence.')
    evidence = read_private(evidence_path)
    required = {'version','profile_id','source_kind','source_sha256','media_sha256','caption_sha256',
                'title','ui_language','subtitle_language','title_language','caption_language',
                'captured_at','fresh_capture','review_record'}
    if set(evidence) != required or evidence['version'] != 1 or evidence['profile_id'] != profile['profile_id']:
        fail('Publication evidence does not match the configured profile.')
    if evidence['source_kind'] not in ('video','screenshot') or media is None:
        fail('Profile requires approved captured video or screenshot, not a text fallback.')
    media_policy.require_format(platform, media, evidence['source_kind'])
    if media.ai_generated != profile['ai_disclosure']:
        fail('Approved profile disclosure differs from the prepared media.')
    if any(evidence[k] != 'en' for k in ('ui_language','title_language','caption_language')) or evidence['subtitle_language'] not in ('en','none'):
        fail('English UI, title, caption and English-or-absent subtitles require consistent review.')
    # This is a consistency guard, not an automatic language/OCR determination.
    if not text.isascii() or not isinstance(evidence['title'],str) or not evidence['title'].strip() or not evidence['title'].isascii():
        fail('English profile text/title contains unsupported non-ASCII content; review before publication.')
    if not isinstance(evidence['review_record'],str) or not evidence['review_record'].strip():
        fail('A source-language and capture review record is required.')
    captured = evidence['captured_at']
    if type(captured) not in (int,float) or not math.isfinite(captured) or captured > time.time()+60:
        fail('Capture timestamp is invalid.')
    if profile['require_fresh_capture'] and (evidence['fresh_capture'] is not True or (not confirming and time.time()-captured > profile['max_capture_age_hours']*3600)):
        fail('Fresh approved capture evidence is required.')
    path=p.state_path(str(Path(source_path).expanduser()))
    p.check_state_node(path.parent,directory=True)
    info=p.check_state_node(path)
    if info.st_size > p.MAX_VIDEO_BYTES:
        fail('Source capture exceeds the local inspection limit.')
    stamp=p.file_stamp(path)
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(p.CHUNK_BYTES),b''):
            digest.update(chunk)
    if p.file_stamp(path) != stamp:
        fail('Source capture changed during inspection.')
    source_hash=digest.hexdigest()
    media.verify_for_publish()
    if (evidence['source_sha256'] != source_hash or evidence['media_sha256'] != media.sha256 or
            evidence['caption_sha256'] != hashlib.sha256(text.encode()).hexdigest()):
        fail('Publication evidence source, final media or caption hash changed.')
    canonical=json.dumps({'profile':profile,'evidence':evidence},sort_keys=True,separators=(',',':'))
    return {'binding_sha256': hashlib.sha256(canonical.encode()).hexdigest(), 'job_id': 'capture-'+source_hash}
