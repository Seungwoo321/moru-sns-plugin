"""Private, exact Threads delivery binding to a confirmed Instagram rendition."""
import hashlib
import http.client
import ipaddress
import os
import socket
import ssl
import time
import urllib.parse
import urllib.request

import publisher as p
import private_runtime as runtime


def require(ok):
    if not ok:
        raise p.SafeError('Approved video delivery binding could not be verified.')


def public_addresses(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    require(addresses and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses))
    return addresses


def trusted_environment_proxy(host):
    """Opt-in to exactly the inherited, approved HTTPS route; never auto-fallback."""
    expected = os.environ.get('MORU_CDN_PROXY_SHA256')
    allowed = os.environ.get('MORU_CDN_PROXY_HOST')
    if expected is None and allowed is None:
        return None
    require(expected and allowed and host == allowed and allowed == allowed.lower())
    values = [os.environ[n] for n in ('https_proxy','HTTPS_PROXY') if os.environ.get(n)]
    require(values and len(set(values)) == 1)
    value = values[0]
    require(hashlib.sha256(value.encode()).hexdigest() == expected)
    parsed = urllib.parse.urlsplit(value)
    # This deployment supplies HTTP CONNECT for HTTPS. Do not invent TLS-to-proxy
    # handling or accept unrelated proxy protocols when they are not configured.
    require(parsed.scheme == 'http' and parsed.hostname and parsed.port and
            parsed.path in ('','/') and not parsed.query and not parsed.fragment and
            not any(c.isspace() or ord(c)<32 for c in value) and '\\' not in value)
    # Respect inherited NO_PROXY; a bypass must block here, not silently go direct.
    require(not urllib.request.proxy_bypass(host))
    return value


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        addresses = public_addresses(self.host)
        raw = socket.create_connection((addresses[0][4][0], 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def remote_bytes(url, expected_size, expected_sha, expected_type="video/mp4", *, approved_host=None, maximum_size=300_000_000):
    """Exact bytes via pinned direct TLS or an explicitly trusted environment proxy."""
    connection = response = None
    try:
        p.public_video_url(url)
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        require(host == approved_host if approved_host is not None else host.endswith(('.cdninstagram.com', '.fbcdn.net')))
        require(type(maximum_size) is int and 0 < maximum_size <= p.MAX_VIDEO_BYTES)
        require(expected_type in ('video/mp4','image/jpeg'))
        require(type(expected_size) is int and 0 < expected_size <= (8_000_000 if expected_type=='image/jpeg' else maximum_size))
        proxy = trusted_environment_proxy(host)
        context = ssl.create_default_context()  # Includes inherited SSL_CERT_FILE trust.
        require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED)
        headers = {'Accept': expected_type, 'Accept-Encoding': 'identity'}
        if proxy is not None:
            public_addresses(host)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy}),
                urllib.request.HTTPSHandler(context=context),p.NoRedirect())
            # Original hostname is retained for proxy policy, CONNECT and TLS SNI.
            # Do not CONNECT to an IP to circumvent the managed hostname policy.
            response = opener.open(urllib.request.Request(url,method='GET',headers=headers),timeout=30)
        else:
            connection = PinnedHTTPS(host, timeout=30, context=context)
            target = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            connection.request('GET', target, headers=headers)
            response = connection.getresponse()
        require(response.status == 200)
        require(response.getheader('Content-Type', '').split(';')[0].strip().lower() == expected_type)
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
        if proxy is not None:
            public_addresses(host)
    except Exception:
        # Never emit a signed URL, Location header, provider body or query string.
        raise p.SafeError('Approved video delivery fetch failed or bytes changed.') from None
    finally:
        for resource in (response,connection):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass


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
    field = 'thumbnail_url' if binding.get('kind')=='IMAGE' else 'media_url'
    result = p.api_dict(client.request('GET', '/' + p.api_id(binding['post_id']),
                                       params={'fields': 'id,media_type,'+field}))
    require(result.get('id') == binding['post_id'] and result.get('media_type') == 'VIDEO')
    url = p.public_video_url(result.get(field))
    # IMAGE identity is the approved object and bytes, not a rotating signature.
    if binding.get('kind') != 'IMAGE':
        require(review.digest(url) == binding['url_sha256'])
    return url


def verify(ledger, doc, *, video=None, artifacts_dir=None, media_path=None):
    import candidate_review as review
    candidate, binding = doc['candidate'], doc['delivery']
    if candidate['platform'] == 'instagram':
        require(binding is None and (video is None or video.url is None))
        return
    require(candidate['platform'] == 'threads' and isinstance(binding, dict))
    image_mode = binding.get('kind')=='IMAGE'
    required_profile = 'verified_local_image_profile' if image_mode else 'verified_local_profile'
    require(time.time() < binding['expires_at'])
    require(candidate['media_sha256'] == binding['remote_sha256'])
    require(any(i['platform'] == 'instagram' and i['account_id'] == binding['account_id'] and
                i['post_id'] == binding['post_id'] and i['paired_job_id'] == binding['job_id'] and
                i['verdict'] == 'authorized_cross_platform' and
                i['source_kind'] == ('official_public_thumbnail' if image_mode else 'official_public_reencoding') and i['artifact_sha256'] == binding['remote_sha256']
                for i in doc['comparisons']))
    approved_url = official_url(ledger, candidate, binding)
    if video is not None:
        if not image_mode:
            require(video.url == approved_url and review.digest(video.url) == binding['url_sha256'])
        video.verify_for_publish()
        require(video.preflight == required_profile and video.size == binding['bytes'])
        if image_mode:require(isinstance(video,p.Image) and video.width==binding['width'] and video.height==binding['height'])
        require(video.sha256 == binding['remote_sha256'] and review.file_digest(video.path) == binding['remote_sha256'])
    if artifacts_dir is not None:
        require(binding['expires_at'] <= time.time() + 1800)
        path = artifacts_dir / binding['remote_sha256']
        require(review.file_digest(path) == binding['remote_sha256'])
        require(review.file_digest(artifacts_dir / binding['inspection_sha256']) == binding['inspection_sha256'])
        require(media_path is not None)
        video = p.prepare_image(str(media_path),approved_url) if image_mode else p.prepare_video('threads', str(media_path), approved_url)
        require(video.preflight == required_profile and video.sha256 == binding['remote_sha256'] and video.size == binding['bytes'])
        if image_mode:require(video.width==binding['width'] and video.height==binding['height'])
    if image_mode:
        remote_bytes(approved_url,binding['bytes'],binding['remote_sha256'],'image/jpeg')
    else:
        remote_bytes(approved_url,binding['bytes'],binding['remote_sha256'])
    require(time.time() < binding['expires_at'])
    return approved_url


def check_handoff(ledger, key, video):
    """Last non-network check immediately before sending the URL to the provider."""
    import candidate_review as review
    row = ledger.db.execute('SELECT document,document_sha256 FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?', key).fetchone()
    require(row and review.digest(row['document']) == row['document_sha256'])
    import json
    binding = json.loads(row['document'])['delivery']
    require(time.time() < binding['expires_at'])
    if binding.get('kind') == 'IMAGE':
        proof = getattr(ledger, 'image_delivery_check', None)
        ledger.image_delivery_check = None  # one create handoff, never persisted
        require(proof == (key, row['document_sha256'], video.sha256, review.digest(video.url)))
    else:
        require(review.digest(video.url) == binding['url_sha256'])
    require(video.sha256 == binding['remote_sha256'])
    video.verify_for_publish()
