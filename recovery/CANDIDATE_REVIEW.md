# Exact-candidate review: evidence contract and execution

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
comparison report. Name each evidence file by its actual SHA-256 in one private
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
  "version": 1,
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
