# Historical one-pair Threads IMAGE compatibility

New video-pair IMAGE approvals are disabled by the source-first media policy.
This document describes previously stored v0.1.6 approvals only; it is not a
new fallback procedure. Keep prior rows and GET-only receipt confirmation intact.

This path uses ONLY the confirmed own Instagram Reel's official `thumbnail_url`.
Do not submit the rejected video, create another host, or recreate the pair/ledger.
The caller supplies the exact private JPEG downloaded through the approved route,
its SHA, size, dimensions and an explicitly approved still-image caption.

The native Threads request uses `media_type=IMAGE` and `image_url` per
[Meta's official publishing reference](https://developers.facebook.com/documentation/threads/reference/publishing).
Container status is read before publication. The final receipt must return the
expected ID, a valid permalink and `media_type=IMAGE`; otherwise the attempt is
quarantined with any returned post ID retained and cannot automatically retry.
A successful POST ID is first durably stored as `confirming_receipt`, excluded
from metrics and publication success. If interrupted there, the same approved
job can resume only account/receipt GETs; it cannot POST again. Temporary GET
failure retains confirmation-pending state; a mismatched receipt becomes unknown.
This uses the existing state column and needs no ledger schema migration.

## Private review schema 3

Start with the existing candidate review format and current live history after
the confirmed IG second Reel. Keep the SAME runtime, pair, Threads job, accounts,
all existing history and legacy uncertainty. Change:

- `version`: 3 (private review format, not a v2 product).
- Add `fallback_approval_record`: the user's approval record for a screenshot and
  its exact revised caption. Add `fallback_report_sha256`: the hash of a private
  report binding the still, caption, own IG receipt, and source provenance.
- `candidate.media_sha256`: the actual JPEG SHA; `text_sha256`: the exact canonical
  still-caption SHA. Compute `content_hash` using native `content_hash(text, image)`
  or the native offline IMAGE plan. It uses `image_sha256`, not a video hash.
- `delivery`: retain account_id, confirmed IG post_id, successful IG job_id,
  source_sha256 (the actual local bytes uploaded to IG), url_sha256, remote_sha256,
  bytes, inspection_sha256, expires_at; add `kind: "IMAGE"`, `width`, `height`.
  URL digest is SHA-256 of the entire exact official `thumbnail_url`, including
  its query, stored ONLY as a historical observation digest. For IMAGE it is NOT
  an admission identity: signatures rotate. The approved own IG account, media ID,
  published native receipt and exact JPEG bytes remain immutable. Never use media_url here.
- The paired IG comparison must be `authorized_cross_platform`, with the exact
  successful IG job, `source_kind: "official_public_thumbnail"` and the JPEG's
  artifact SHA. Retain all other historical comparisons and evidence.

For the current operational screenshot, the provided dimensions/size are 640×1136
and 75034 bytes. Supply its ACTUAL SHA; the implementation does not invent it.
The own IG ID must match the confirmed native receipt. A private report must state
that this is a still frame, without describing the attachment as a playing video.
Software verifies the evidence hashes, official receipt relation and image bytes;
caption meaning and visual/source review remain trusted operator findings.

Keep the JPEG as a .jpg/.jpeg candidate and an evidence copy named by SHA, along
with each report file named by SHA, in the existing 0700/0600 private directories.
JPEG markers, actual codec/dimensions, size and file hash are checked. There is no
video/edit-list inspection or video upload in this path.

## Register atomically, without publication

```sh
python3 recovery/reconcile.py approve-candidate \
  --state-dir /private/EXISTING-runtime \
  --pair-id EXISTING_PAIR_ID \
  --review /private/image-review.json \
  --image-file /private/official-thumbnail.jpg \
  --text-file /private/still-caption.txt \
  --artifacts-dir /private/evidence
```

This performs official GETs and remote JPEG validation, then atomically inserts
the image review, its pair association and a digest-bound image/caption approval
in existing recovery_metadata. The original pair and its video-caption hash remain
unchanged. Once accepted, the Threads member is restricted to that exact IMAGE
review; the old video route cannot reuse this pair. No schema migration is needed.

The Threads member must have NO existing review or job. A previous validation
failure before insertion is compatible; any real attempt, including unknown or
failed, blocks fallback rather than being deleted/reset. IG's successful receipt,
legacy unknowns, false global inventory, final ordinal and single-writer lock remain.

## Approved environment HTTPS proxy

By default direct CDN TLS remains pinned. For the verified normal environment
route, set BOTH per-process opt-in values:

- `MORU_CDN_PROXY_HOST`: the exact hostname returned in the official thumbnail URL.
- `MORU_CDN_PROXY_SHA256`: SHA-256 of the exact inherited HTTPS_PROXY/https_proxy
  value. If both variants exist they must match. Do not print/copy credentials.

Keep inherited proxy variables, NO_PROXY and CA variables unchanged. This deployment
uses HTTP CONNECT for HTTPS; other proxy schemes are rejected. Existing NO_PROXY
bypass for the CDN blocks rather than silently opening a direct connection.
The approved proxy remains responsible for its own destination-policy/DNS enforcement.
The client additionally checks public-only DNS results before/after transfer, keeps
the original hostname for proxy policy, CONNECT and certificate/SNI verification,
and retains the environment CA trust. It does not CONNECT to an IP to evade policy.

Only official-returned CDN hosts are accepted. No redirect, alternate URL/host,
proxy fallback, private/mixed DNS target or direct retry after proxy denial is
allowed. Response must be 200, image/jpeg, identity-encoded, and exactly match the
approved byte count and SHA. Signed URLs and proxy credentials are never logged.

## Execute in the authorized operator session

Use native `plan` with these same flags first. Plan is offline, not live admission.

```text
publisher.py publish --platform threads --state-dir /private/EXISTING-runtime
  --job-id EXISTING_THREADS_JOB --candidate-pair-id EXISTING_PAIR_ID
  --text-file /private/still-caption.txt --image-file /private/official-thumbnail.jpg
  --execute
```

Omit --image-url. Each registration and publish invocation resolves thumbnail_url
directly from the official API for the approved own IG object and verifies its
exact bytes through the approved HTTPS route. Only that invocation's checked URL
is handed to Threads in memory; any legacy caller URL is ignored, never trusted.
A one-use in-memory digest proof protects the create handoff. No signed URL is
persisted or logged, and no existing state migration is needed. No --video-file
or --video-url is accepted alongside the image.
Run the same job again when the native result is media_processing and due.
For confirming_receipt, rerun the same approved job for GET-only receipt
confirmation; this cannot create another container or publication POST.
Unknown responses never authorize a new attempt. Fresh official history, exact
image/body/delivery binding, six-hour target-account interval and ordinal 3 are
rechecked on each invocation. No recurring schedule or future pair is enabled.

Tests use synthetic local JPEGs and mock provider/proxy responses, including native
IMAGE completion and wrong final media-type quarantine. The implementation session
does not read/modify the operational journal or make live account writes.
