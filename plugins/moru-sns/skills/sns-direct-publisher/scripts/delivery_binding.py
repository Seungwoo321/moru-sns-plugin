"""Private, exact Threads delivery binding to a confirmed Instagram rendition."""
import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

import publisher as p
import private_runtime as runtime


def require(ok):
    if not ok:
        raise p.SafeError('Approved video delivery binding could not be verified.')


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        require(addresses and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses))
        raw = socket.create_connection((addresses[0][4][0], 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def remote_bytes(url, expected_size, expected_sha):
    """No redirects/proxy/cookies/auth. Resolve public addresses then pin TLS socket."""
    connection = None
    try:
        p.public_video_url(url)
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        require(host.endswith(('.cdninstagram.com', '.fbcdn.net')))
        require(type(expected_size) is int and 0 < expected_size <= 300_000_000)
        connection = PinnedHTTPS(host, timeout=30, context=ssl.create_default_context())
        target = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
        connection.request('GET', target, headers={'Accept': 'video/mp4', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        require(response.status == 200)
        require(response.getheader('Content-Type', '').split(';')[0].strip().lower() == 'video/mp4')
        require(response.getheader('Content-Encoding', 'identity').lower() == 'identity')
        length = response.getheader('Content-Length')
        require(length is None or int(length) == expected_size)
        digest, total = hashlib.sha256(), 0
        while True:
            chunk = response.read(min(1024 * 1024, expected_size - total + 1))
            if not chunk:
                break
            total += len(chunk)
            require(total <= expected_size)
            digest.update(chunk)
        require(total == expected_size and digest.hexdigest() == expected_sha)
    except Exception:
        # Never emit a signed URL, Location header, provider body or query string.
        raise p.SafeError('Approved video delivery fetch failed or bytes changed.') from None
    finally:
        if connection is not None:
            connection.close()


def official_url(ledger, candidate, binding):
    import candidate_review as review
    marker = p.read_state_marker(ledger.directory / 'runtime.json')
    account = marker['accounts'].get('instagram')
    require(account and account['account_id'] == binding['account_id'])
    previous = ledger.get(('instagram', binding['account_id'], binding['job_id']))
    require(previous and previous['state'] == 'published' and previous['post_id'] == binding['post_id'])
    source = ledger.db.execute('SELECT job_id FROM recovery_media WHERE platform=? AND sha256=?',
                               ('instagram', binding['source_sha256'])).fetchone()
    require(source and source[0] == binding['job_id'])
    config = runtime.load_environment('instagram')
    require(config['expected_user_id'] == account['account_id'] and
            config['expected_username'] == account['username'] and config['instagram_login_mode'] == account['auth_mode'])
    client = p.APIClient('instagram', config['access_token'], account['auth_mode'])
    # The caller already checked complete account history under the writer lock.
    result = p.api_dict(client.request('GET', '/' + p.api_id(binding['post_id']),
                                       params={'fields': 'id,media_type,media_url'}))
    require(result.get('id') == binding['post_id'] and result.get('media_type') == 'VIDEO')
    url = p.public_video_url(result.get('media_url'))
    require(review.digest(url) == binding['url_sha256'])
    return url


def verify(ledger, doc, *, video=None, artifacts_dir=None, media_path=None):
    import candidate_review as review
    candidate, binding = doc['candidate'], doc['delivery']
    if candidate['platform'] == 'instagram':
        require(binding is None and (video is None or video.url is None))
        return
    require(candidate['platform'] == 'threads' and isinstance(binding, dict))
    require(time.time() < binding['expires_at'])
    require(candidate['media_sha256'] == binding['remote_sha256'])
    require(any(i['platform'] == 'instagram' and i['account_id'] == binding['account_id'] and
                i['post_id'] == binding['post_id'] and i['paired_job_id'] == binding['job_id'] and
                i['verdict'] == 'authorized_cross_platform' and
                i['source_kind'] == 'official_public_reencoding' and i['artifact_sha256'] == binding['remote_sha256']
                for i in doc['comparisons']))
    approved_url = official_url(ledger, candidate, binding)
    if video is not None:
        require(video.url == approved_url and review.digest(video.url) == binding['url_sha256'])
        video.verify_for_publish()
        require(video.preflight == 'verified_local_profile' and video.size == binding['bytes'])
        require(video.sha256 == binding['remote_sha256'] and review.file_digest(video.path) == binding['remote_sha256'])
    if artifacts_dir is not None:
        require(binding['expires_at'] <= time.time() + 1800)
        path = artifacts_dir / binding['remote_sha256']
        require(review.file_digest(path) == binding['remote_sha256'])
        require(review.file_digest(artifacts_dir / binding['inspection_sha256']) == binding['inspection_sha256'])
        require(media_path is not None)
        video = p.prepare_video('threads', str(media_path), approved_url)
        require(video.preflight == 'verified_local_profile' and video.sha256 == binding['remote_sha256'] and video.size == binding['bytes'])
    remote_bytes(approved_url, binding['bytes'], binding['remote_sha256'])
    require(time.time() < binding['expires_at'])


def check_handoff(ledger, key, video):
    """Last non-network check immediately before sending the URL to the provider."""
    import candidate_review as review
    row = ledger.db.execute('SELECT document,document_sha256 FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?', key).fetchone()
    require(row and review.digest(row['document']) == row['document_sha256'])
    import json
    binding = json.loads(row['document'])['delivery']
    require(time.time() < binding['expires_at'] and review.digest(video.url) == binding['url_sha256'])
    require(video.sha256 == binding['remote_sha256'])
    video.verify_for_publish()
