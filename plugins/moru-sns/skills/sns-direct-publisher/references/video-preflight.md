# Final originals and platform-specific video checks

Prepare the user-approved final original for each intended platform from the
approved capture. A new encoding is a new byte hash requiring the existing source,
content and candidate review. Do not substitute an Instagram recompression,
thumbnail, public Slack link, or another file after approval. No automatic encoding,
hosting, public sharing, format downgrade or new upload route is introduced here.

`publisher.py plan` now reports `platform_video_preflight` for the requested
platform only. It separates local container/codec checks from delivery, never
certifies the other platform, never fetches a URL, and always leaves
`publish_ready:false`. A URL string alone is not delivery proof.

The existing operating profile remains 30–150 seconds, H264/yuv420p, 23–60 fps,
width <=1920, optional AAC <=48kHz mono/stereo. New native Meta video jobs also
require a structurally readable MP4, no edit-list boxes and moov before media.
These checks run before account API calls, job claim or container creation.
They do not claim to certify closed GOP, VBR mode, the documented 128kbps audio
encoding target, all encoder details or provider acceptance. Prepare those to the
official target and review the encoding provenance; metadata pass is not complete
platform certification. For audio, do not describe the existing codec/rate/channel
checks as proof of the 128kbps encoder target.

Official specifications checked on 2026-11-24:
- [Threads video specifications](https://developers.facebook.com/documentation/threads/posts#media-specifications)
- [Instagram Reel specifications](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media#reels-specs)

Both prohibit edit lists and require moov at the front. Both specify AAC at up to
48kHz, one or two channels and 128kbps audio. Keep platform bitrate and size limits
separate. This plugin's narrower duration profile is not the provider limit.

For a **new** native video job, Instagram Unified resumable sends the checked local
bytes. Its upload body rechecks the exact hash/size while streaming to the official
endpoint; the plan labels this route available, not sent or accepted. Existing
approved original HTTPS routes for both Threads and Instagram remain supported:
set `MORU_VIDEO_URL_APPROVAL` to a private JSON file outside Git/plugin trees
(directory 0700, file 0600), with exactly these fields:

```text
version:1, platform, account_id, job_id, media_sha256, url_sha256,
content_sha256, approval_record, final_original:true, expires_at
```

The user/operator approval is for this exact existing account and final original,
not permission to create hosting. `url_sha256` hashes the exact supplied URL (do
not persist a signed URL in this JSON); `content_sha256` is native
`content_hash(caption,video)` without a profile prefix. `expires_at` is a UTC epoch
no more than 30 minutes ahead. Existing profile/ledger approvals remain separate
and mandatory wherever configured; this file does not bypass recovery admission.

Before a new claim/container, the publisher checks that binding and streams a
read-only GET of the exact URL, requiring matching size, SHA and video/mp4 MIME.
It retains public-address DNS validation, original-host TLS, no redirects,
identity encoding, bounded size and the explicitly approved environment proxy
policy. No credential is sent to the media host and errors omit the URL. The
same immutable local bytes already passed target-platform preflight, so a matching
remote digest binds those results to the fetched copy. A different encoding fails
rather than inheriting local results. This is a point-in-time check, not a guarantee
that a remote host cannot change later or Meta will accept the upload.

Offline plan remains unverified for HTTPS delivery and does not fetch. Missing
URLs report `official_original_transfer_path_missing` / `공식 원본 전송 경로 없음`;
missing/expired/mismatched account approval and failed byte fetch have distinct
errors before container creation. No automatic public sharing, alternate host or
media substitution is performed. Read-only MP4 inspection uses POSIX O_RDONLY,
O_NOFOLLOW, regular-file checks and before/after metadata consistency.

Historical job rows and recovery approvals are not rewritten. Existing recorded
jobs retain their existing resume/unknown/receipt handling; this change is not an
authorization to resume old work. Historical recovery URL-binding code still checks
exact reviewed bytes with DNS/TLS/host/status/size controls, but it is not evidence
of an approved new original-video transfer route. A successful Instagram upload
or public rendition does not certify Threads encoding or delivery. Neither direct
Slack video delivery nor a Threads local-byte upload feature has been established
by this change. Remote API capabilities are not expanded.
