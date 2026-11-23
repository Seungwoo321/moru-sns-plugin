"""Single-owner bridge to the existing engine. No scheduling or state creation."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/sns-direct-publisher/scripts'
sys.path.insert(0, str(SCRIPTS))
import private_runtime as runtime
import publisher as p


class Blocked(Exception):
    """A deliberately value-free failure at the remote boundary."""


@dataclass(frozen=True)
class Media:
    path: Path
    sha256: str
    video_kind: str = 'real-demo'
    audio_kind: str = 'original'
    ai_generated: bool = False


class Engine:
    def __init__(self, *, state_dir, runtime_id, bindings, media=None, publishing_enabled=False):
        if not bindings or set(bindings) - {'threads', 'instagram'}:
            raise ValueError('Only explicit Threads/Instagram bindings are supported.')
        self.state_dir = Path(state_dir)
        self.runtime_id = runtime_id
        self.bindings = {k: dict(v) for k, v in bindings.items()}
        self.media = dict(media or {})
        self.publishing_enabled = publishing_enabled

    def _config(self, platform):
        if platform not in self.bindings:
            raise Blocked('platform_not_enabled')
        config = runtime.load_environment(platform)
        actual = {'account_id': config['expected_user_id'], 'username': config['expected_username'],
                  'auth_mode': config['instagram_login_mode']}
        if actual != self.bindings[platform]:
            raise Blocked('configured_account_mismatch')
        if platform == 'instagram' and actual['auth_mode'] != 'unified':
            raise Blocked('instagram_unified_required')
        return config

    def _guard(self, platform, config):
        p.inspect_state(self.state_dir, self.runtime_id)
        marker = p.read_state_marker(self.state_dir / 'runtime.json')
        if marker['accounts'].get(platform) != self.bindings[platform]:
            raise Blocked('ledger_account_mismatch')
        p.require_state_binding(self.state_dir, platform, config['expected_user_id'], config['instagram_login_mode'])

    @contextmanager
    def _locked(self, platform, config):
        # Lock the original marker: never create a replacement ledger or lock file.
        self._guard(platform, config)
        marker = self.state_dir / 'runtime.json'
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if p.file_identity(os.fstat(fd)) != p.file_identity(marker.lstat()):
                raise Blocked('runtime_marker_changed')
            self._guard(platform, config)
            yield
        finally:
            os.close(fd)

    def _content(self, platform, text, media_id):
        text = p.validate_text(platform, text)
        video = None
        if media_id is not None:
            # Minimal transport: text Threads, registered immutable Reels only.
            if platform != 'instagram' or media_id not in self.media:
                raise Blocked('media_not_registered')
            asset = self.media[media_id]
            path = p.state_path(str(asset.path))
            p.check_state_node(path)
            video = p.prepare_video(platform, str(path), None, asset.ai_generated,
                                    asset.video_kind, asset.audio_kind, allow_missing_url=True)
            if video.sha256 != asset.sha256:
                raise Blocked('media_hash_mismatch')
        if platform == 'instagram' and video is None:
            raise Blocked('registered_reel_required')
        return text, video, p.content_hash(text, video)

    def _publication_gate(self, platform, account_id, job_id, digest):
        uri = (self.state_dir / 'publisher.sqlite3').as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT job_id, content_hash, state, updated_at FROM jobs '
                              'WHERE platform=? AND account_id=? AND reply_to_id IS NULL',
                              (platform, account_id)).fetchall()
        now = time.time()
        for old_id, old_hash, state, updated in rows:
            if old_id == job_id:
                if old_hash != digest:
                    raise Blocked('job_content_mismatch')
                continue  # Engine owns same-job retry/unknown/processing semantics.
            if old_hash == digest:
                raise Blocked('content_already_recorded')
            if state not in ('published', 'failed', 'duplicate'):
                raise Blocked('unresolved_prior_job')
            if state == 'published' and (updated is None or now - updated < 6 * 3600):
                raise Blocked('minimum_interval_not_elapsed')

    def call(self, operation, platform, job_id=None, text=None, media_id=None, reviewed_hash=None):
        """No remote-supplied credentials, paths, runtime IDs, URLs or shell arguments."""
        try:
            if operation not in ('status', 'check', 'plan', 'publish', 'metrics'):
                raise Blocked('unsupported_operation')
            if platform not in self.bindings:
                raise Blocked('platform_not_enabled')
            if operation == 'status':
                diagnosis = runtime.inspect_environment(platform)
                # State and identity are checked separately, never inferred from doctor.
                return {'configuration': diagnosis, 'publishing_enabled': self.publishing_enabled,
                        'account_verified': False, 'ledger_verified': False, 'scheduled_run_verified': False}
            config = self._config(platform)
            if operation in ('plan', 'publish', 'metrics') and (
                    not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', job_id)):
                raise Blocked('invalid_job_id')
            if operation == 'publish' and not self.publishing_enabled:
                raise Blocked('publishing_disabled')
            with self._locked(platform, config):
                if operation in ('plan', 'publish'):
                    text, video, digest = self._content(platform, text, media_id)
                    if operation == 'plan':
                        return {'platform': platform, 'job_id': job_id, 'content_hash': digest,
                                'media_id': media_id, 'publish_ready': False, 'network_requests': 0}
                    if reviewed_hash != digest:
                        raise Blocked('reviewed_hash_mismatch')
                    self._publication_gate(platform, config['expected_user_id'], job_id, digest)
                if operation in ('check', 'publish', 'metrics'):
                    runtime.verify_identity(platform, config['access_token'], config['expected_username'],
                                            config['expected_user_id'], config['instagram_login_mode'])
                if operation == 'check':
                    return {'platform': platform, 'account_matches': True, 'ledger_verified': True,
                            'publishing_permissions_verified': False, 'scheduled_run_verified': False}
                ledger = p.Ledger(str(self.state_dir))
                try:
                    engine = p.Publisher(platform, config['expected_user_id'],
                                         p.APIClient(platform, config['access_token'], config['instagram_login_mode']),
                                         ledger, instagram_login_mode=config['instagram_login_mode'],
                                         instagram_upload_mode='resumable' if platform == 'instagram' else None)
                    result = (engine.publish(job_id, text, video) if operation == 'publish' else engine.metrics(job_id))
                finally:
                    ledger.close()
                allowed = ('platform', 'job_id', 'state', 'post_id', 'permalink', 'next_poll_at',
                           'network_requests_skipped', 'metrics', 'collected_at')
                result = {k: v for k, v in result.items() if k in allowed}
                # Prevent credentials in remote API fields escaping even through unexpected responses.
                import json
                rendered = json.dumps(result)
                for key in ('THREADS_USER_ACCESS_TOKEN', 'INSTAGRAM_USER_ACCESS_TOKEN'):
                    if os.environ.get(key):
                        rendered = runtime.redact(rendered, os.environ[key])
                return json.loads(rendered)
        except Blocked:
            raise
        except Exception:
            # Do not relay paths, tokens, provider responses or arbitrary exceptions.
            raise Blocked('engine_check_failed_no_automatic_retry') from None
