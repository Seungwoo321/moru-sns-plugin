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

For a **new** native video job, only Instagram Unified local resumable delivery is
currently admitted. Its upload body rechecks the exact local hash/size while
streaming to the official endpoint; the plan labels this route available, not
already sent or accepted. AI and final receipt confirmation remain mandatory under
the configured profile. A valid local file with no proven original delivery route
is held with `official_original_transfer_path_missing` / `공식 원본 전송 경로 없음`.
This includes new Threads URL jobs and unproven Instagram URL delivery.

Historical job rows and recovery approvals are not rewritten. Existing recorded
jobs retain their existing resume/unknown/receipt handling; this change is not an
authorization to resume old work. Historical recovery URL-binding code still checks
exact reviewed bytes with DNS/TLS/host/status/size controls, but it is not evidence
of an approved new original-video transfer route. A successful Instagram upload
or public rendition does not certify Threads encoding or delivery. Neither direct
Slack video delivery nor a Threads local-byte upload feature has been established
by this change. Remote API capabilities are not expanded.
