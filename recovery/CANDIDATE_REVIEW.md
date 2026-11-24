# Exact-candidate review: evidence contract and execution

For the explicitly authorized single pair with incomplete global inventory, see
[ONE_PAIR.md](ONE_PAIR.md). This separate manual admission does not change the
ordinary recovery or scheduled/bridge gates described below.

This is an implemented, operator-reviewed v1 path. It never restores an unknown
old original hash from a CDN rendition. Human material comparison remains an
evidence input: the tool verifies file digests, complete receipt coverage, scope,
live history and one-use consumption, not the truth of a visual judgment.
Do not mark a partial, ambiguous, duplicate or mismatching comparison `distinct`.

## Preconditions and private files

Deploy the reviewed PR code consistently to every writer before use. Stop old
writers and route all writers to ONE persistent private runtime outside Git.
Only runtimes created by this revision's `migrate` contain `recovery_reviews`.
There is intentionally no in-place schema patch of an existing runtime. If any
operational migrated runtime already exists, stop and design its explicit upgrade;
do not create a second ledger to evade existing attempts or reservations.

Keep the candidate MP4, exact UTF-8 caption, review JSON and evidence artifacts
under approved private paths outside Git. Directories must be 0700, files 0600,
owned by the executor, with no symlinks/hard links. Files must remain immutable.
No tool in this workflow hosts, uploads or publishes media. Actual publisher
preflight and official transport requirements continue to apply later.

Each comparison needs the complete historical media artifact and a full private
comparison report. Keep a .mp4 candidate file for ffprobe. Name each evidence copy by its actual SHA-256 in one private
artifact directory. The report must identify the exact new candidate, historical
post, observed scene/event differences, full footage coverage and provenance.
For carousels, use a private immutable archive containing ALL members plus their
receipt mapping; incomplete archives must not be approved. For text posts, retain
the official text response and a text/source-event comparison report.

For `official_public_reencoding`, retain the official GET receipt/download audit
in the referenced private record, including the post-to-rendition association.
The artifact SHA is explicitly the downloaded rendition digest, NOT the old source
file hash. Do not put signed URLs, tokens, secrets or private Library URLs into
review JSON, source control or output. `verified_original` requires an independently
reviewed original-to-receipt chain; mere file possession is insufficient.

## Read-only snapshot

With the two normal approved connections available:

```sh
python3 recovery/reconcile.py review-snapshot --state-dir /private/native-runtime
```

This holds the same writer lock and queries BOTH bound accounts, including bounded
pagination via fixed API edges. It returns sanitized `history` and its digest.
Use the returned history objects unchanged; do not type IDs or times from memory.
Every object contains `platform`, `account_id`, `post_id`, `published_at` (epoch
seconds), `permalink`, `media_type`, and `text_sha256`. The full official ID/link set
must agree with existing native published receipts. Drift is not auto-imported.

## Review JSON (placeholders, not an authorization)

```json
{
  "version": 2,
  "runtime_id": "EXACT_EXISTING_RUNTIME_ID",
  "review_id": "UNIQUE_REVIEW_RECORD",
  "approval_record": "USER_APPROVAL_RECORD_ID",
  "reviewer": "REVIEWING_OPERATOR_ID",
  "candidate": {
    "platform": "instagram",
    "account_id": "EXACT_BOUND_ACCOUNT_ID",
    "job_id": "EXACT_ONE_TIME_JOB_ID",
    "media_sha256": "SHA256_OF_ACTUAL_NEW_MP4",
    "content_hash": "NATIVE_PUBLISHER_CONTENT_HASH",
    "text_sha256": "SHA256_OF_EXACT_CAPTION_FILE",
    "source_record": "ACTUAL_CAPTURE_RECORD_ID",
    "ai_generated": false
  },
  "delivery": null,
  "history": [],
  "comparisons": [
    {
      "platform": "instagram",
      "account_id": "EXACT_HISTORICAL_ACCOUNT_ID",
      "post_id": "OFFICIAL_POST_ID",
      "source_kind": "official_public_reencoding",
      "artifact_sha256": "SHA256_OF_COMPLETE_DOWNLOADED_RENDITION",
      "receipt_source_record": "OFFICIAL_GET_AND_DOWNLOAD_RECORD_ID",
      "comparison_record": "FULL_COMPARISON_REPORT_RECORD_ID",
      "report_sha256": "SHA256_OF_PRIVATE_COMPARISON_REPORT_FILE",
      "complete": true,
      "verdict": "distinct",
      "paired_job_id": null
    }
  ]
}
```

Replace `history` with the ENTIRE snapshot, and provide one comparison per history
item across BOTH platforms. An empty list is valid only if official history is
actually empty. Text posts use `source_kind: official_text`. Identifiers are plain
record identifiers, not arbitrary notes or URLs. Compute `content_hash` using the
existing publisher's `content_hash(exact_text, prepared_video)`; it includes the
video SHA and AI flag. The candidate is video-only, not a text-only escape hatch.

## Register without publication

```sh
python3 recovery/reconcile.py approve-candidate \
  --state-dir /private/native-runtime \
  --review /private/review.json \
  --media-file /private/approved.mp4 \
  --text-file /private/caption.txt \
  --artifacts-dir /private/comparison-artifacts
```

Registration recomputes all local artifact hashes, checks native content binding,
requeries full official history and inserts an immutable unique review while
holding the native writer lock. It rejects unknown attempts and incomplete writer
or inventory attestations. Re-registration, another plan for the same platform/
account/media, and an existing job are rejected. There is no replace/reset flag.

Later, the existing native publisher needs no bypass argument. For this exact job
it requeries BOTH histories and rechecks the review digest before claiming. Only
the unknown-historical-media gate can be satisfied by the review. Its state changes
from `approved` to `consumed` in the same transaction as job/media reservation.
Processing may resume that same job; unknown or failed outcomes cannot consume
the authorization again or start another plan. Source, text, account, job, or
history changes block. Local preflight, duplicate detection, six-hour target-account
interval, unresolved-attempt quarantine and writer ownership remain mandatory.

## Instagram followed by Threads

Approve and complete the Instagram job first through the separate authorized
execution. Obtain a NEW full snapshot after the native job has its confirmed
post ID and public permalink. Create a separate exact Threads review. The new
Instagram item must use `verdict: authorized_cross_platform` and `paired_job_id`
equal to that actual Instagram job. It must have the same locally recorded source
SHA and a successful native receipt. Keep its actual rendition artifact/report.
All other history comparisons remain `distinct`. This is an explicitly reviewed
paired reuse, not a claim that the newly published video is different footage.
Same-platform reuse and unknown/missing paired jobs are rejected. The Instagram
time does not reset the Threads account's six-hour clock.

## Tests and limitations

Offline tests cover real native claim/resume paths with mocked providers, full
history changes on either platform, altered media/text/account/job scope, unknown
attempts, duplicate review/plan use, source provenance retention, actual file digest
checks, full pagination, paired cross-platform scope, and native writer contention.
They are not real posts or proof that a human comparison has been completed.
Local locks still do not fence separate container copies; deployment must provide
one shared ledger and retire old writers. No visual comparison is fabricated here.


## Threads delivery binding (review schema 2)

For Instagram, `delivery` is null and execution requires Unified Login local
`resumable` upload with no URL. Threads delivery failures cannot consume an
Instagram review. Old schema-1 reviews lack delivery binding and are rejected.
The schema number here is a private review-document format, not a v2 service.

For Threads, `candidate.media_sha256` is the SHA of the EXACT LOCAL DERIVATIVE MP4
provided to `--media-file` during registration and `--video-file` at execution.
Compute its native content hash using that derivative and the Threads text.
Use this object instead of null:

```json
{
  "account_id": "CONFIRMED_INSTAGRAM_ACCOUNT",
  "post_id": "CONFIRMED_INSTAGRAM_POST_ID",
  "job_id": "SUCCESSFUL_INSTAGRAM_NATIVE_JOB",
  "source_sha256": "ORIGINAL_SHA_FROM_INSTAGRAM_LOCAL_UPLOAD_RECEIPT",
  "url_sha256": "SHA256_OF_ENTIRE_EXACT_OFFICIAL_MEDIA_URL",
  "remote_sha256": "SHA256_OF_EXACT_CDN_DERIVATIVE_AND_LOCAL_THREADS_MP4",
  "bytes": 123456,
  "inspection_sha256": "SHA256_OF_PRIVATE_DERIVATIVE_COMPARISON_AND_INSPECTION_REPORT",
  "expires_at": 0
}
```

Use real numeric size and an operator-chosen Unix expiration no later than 30
minutes from registration. This is a conservative local deadline, not a claim
about a provider's signed-URL lifetime. Provider expiration may be earlier and
is detected by the GET. The paired Instagram comparison must use
`official_public_reencoding`, the same derivative artifact SHA, and the exact
successful native job. The report must compare the original approved footage
with this derivative and retain its format inspection evidence. Keep the derivative
copy at `artifacts-dir/<remote_sha256>` and report at
`artifacts-dir/<inspection_sha256>` as well as the candidate .mp4 file.

Registration retrieves media_url only from the fixed official Graph endpoint for
that confirmed Instagram post and checks its full URL digest. The URL itself,
including signed query parameters, is never stored in review JSON or printed.
Only an official-returned cdninstagram.com/fbcdn.net subdomain is fetched. The
transport rejects private/mixed DNS results, pins a public IP with hostname TLS
verification, uses no proxy/cookies/auth headers, and never follows redirects.
It requires a complete 200 video/mp4 identity-encoded response with the exact
approved length and SHA. The local derivative must also pass the native ffprobe
profile at registration; execution rechecks the same local bytes/profile and
fresh official URL/CDN bytes before claiming or resuming.

Pass precisely that privately retained URL as the native Threads video URL.
A different URL or even changed signed query fails. A final hash/deadline check
runs immediately before the container POST. URLs are not silently rebound after
rotation. Expired reviews cannot be reset or overwritten by this implementation;
stop for an explicit safe re-review/renewal design rather than creating a new plan.

The actual returned CDN hostname must be allowed by the selected environment.
A network denial is a block, never a reason to use alternate hosting or weaken
verification. This session tests transport with mock responses, not a real CDN.
A provider can change availability after our GET and before Meta fetches the URL;
preflight does not guarantee asynchronous provider fetch success. Existing unknown
outcome quarantine continues to apply, without automatic resubmission.
