# Explicit one-pair v1 admission

For the explicitly approved Threads still-image fallback and trusted environment
HTTPS proxy, see [IMAGE_FALLBACK.md](IMAGE_FALLBACK.md).

This is an implementation for the current explicitly authorized pair only:
Instagram's second Reel, then Threads' third root video. It is not a declaration
that the complete historical attempt inventory has been recovered. The source
must be the actual file obtained from one of the two approved Slack report DMs.

## Policy and prerequisites

The ordinary publisher and scheduled/bridge paths retain their recovery gates.
Only the native CLI's explicit `--candidate-pair-id` selects `claim_pair`. The
runtime must still have `inventory_complete=false`, `old_writers_stopped=true`,
one shared private ledger and a single active writer. The existing exclusive
ledger flock covers live checks, the claim and provider calls. A separate container
copy is not protected by a local flock; the operator must supply actual single-
writer/retirement evidence. No schedule is enabled by this feature.

The trusted operator must review the complete available work record specifically
for this new Slack file and certify that it has had no write attempts. This is a
candidate-specific attestation, not a fact inferred from public posts or absence
of local records. The evidence file must bind the Slack message/file identifiers,
actual source SHA, both account/job/body definitions, reviewed execution records,
no-write findings and single-writer evidence. Do not fabricate that report.

All known unresolved legacy jobs must be enumerated with separate evidence that
they concern different material. The tool stores their full rows unchanged and
checks them on every scoped admission. It does not mark them failed or completed.
Matching claimed legacy source/content hashes, unlisted unresolved jobs, changed
legacy rows or any unknown attempt by either candidate block the pair.

The distinction is intentional: unresolved OLD material may remain, while the
specific new pair has a complete no-attempt audit. This does not prove that all
old provider requests have settled. A new or changed public receipt invalidates
the pair snapshot. Cross-provider reads are not an atomic lock on Meta's timeline.

Use the reviewed branch's existing `initialize`/`migrate` commands with truthful
inventory=false and writer-retirement evidence. This revision creates the pair
tables alongside the native recovery state. Do not recreate an operational ledger
to add these tables or bypass attempts; no in-place operational upgrade is provided.

## Private pair manifest

All paths are approved private paths outside Git, directories 0700/files 0600.
Each evidence file is named by its SHA in the private artifacts directory.
Caption files must contain the exact canonical native text: NFC, LF, no leading
or trailing whitespace. Registration rejects text the native CLI would normalize.

```json
{
  "version": 1,
  "runtime_id": "EXISTING_RUNTIME_ID",
  "pair_id": "ONE_FIXED_PAIR_ID",
  "approval_record": "CURRENT_USER_APPROVAL_RECORD",
  "source": {
    "slack_channel": "DSYNTHETIC",
    "slack_message_ts": "ACTUAL_TIMESTAMP_WITH_SIX_DECIMALS",
    "slack_file_id": "FACTUAL_FILE_ID",
    "sha256": "ACTUAL_SLACK_ATTACHMENT_SHA256"
  },
  "candidates": [
    {"platform":"instagram","account_id":"BOUND_IG_ACCOUNT","job_id":"FIXED_IG_JOB","ordinal":2,"text_sha256":"EXACT_IG_TEXT_SHA256"},
    {"platform":"threads","account_id":"BOUND_THREADS_ACCOUNT","job_id":"FIXED_THREADS_JOB","ordinal":3,"text_sha256":"EXACT_THREADS_TEXT_SHA256"}
  ],
  "history": [],
  "candidate_never_attempted": true,
  "single_writer_confirmed": true,
  "evidence_sha256": "PRIVATE_CANDIDATE_WORK_AUDIT_SHA256",
  "legacy_unresolved": [
    {"platform":"threads","account_id":"BOUND_THREADS_ACCOUNT","job_id":"ACTUAL_LEGACY_UNKNOWN_JOB","exclusion_evidence_sha256":"PRIVATE_DIFFERENT_MATERIAL_EVIDENCE_SHA256"}
  ]
}
```

`DSYNTHETIC` is a documentation placeholder, never an actual source approval. Set
`MORU_APPROVED_SLACK_CHANNELS` to a private JSON array of explicitly approved DM
IDs for new legacy-pair registration. Missing or invalid configuration blocks new
registration. Existing saved pair proofs are not rewritten. The
manifest's `history` is the exact existing three-receipt official snapshot, in the
same format as [CANDIDATE_REVIEW.md](CANDIDATE_REVIEW.md). Do not invent receipts
or include the illustrative legacy job above. Use every actual unresolved job;
use an empty list only when the existing ledger has none, preserving inventory=false.

## GET-only preparation

```sh
python3 recovery/reconcile.py register-pair \
  --state-dir /private/native-runtime \
  --pair-manifest /private/pair.json \
  --media-file /private/slack-original.mp4 \
  --instagram-text-file /private/instagram.txt \
  --threads-text-file /private/threads.txt \
  --artifacts-dir /private/evidence
```

This checks real source/body/evidence hashes, exact bound accounts, live official
history and the starting counts (IG=1, Threads=2), then inserts ONE immutable pair.
There is no replace, reset, second pair or auto-refresh operation. Global recovery
provenance is not modified. The no-attempt audit and legacy snapshot are digest-bound.

Next register the ordinary schema-2 Instagram candidate review from
[CANDIDATE_REVIEW.md](CANDIDATE_REVIEW.md), adding:

```text
--pair-id ONE_FIXED_PAIR_ID
```

The review still requires complete historical material comparisons and exact
source/text binding. It is permanently associated with this pair. Without the
explicit pair ID, both review admission and native execution remain blocked.

## Explicit execution in the separately authorized operator session

Add `--candidate-pair-id ONE_FIXED_PAIR_ID` to the native publisher's existing
`plan` and `publish --execute` invocation. Use the exact fixed job IDs and caption
files. For Instagram use Unified Login, local `--video-file` and resumable mode;
there is no video URL. A plan is offline and reports `pair_live_admission_checked`
as false; only execution performs the fresh admission checks.

The same fixed job can resume only from `media_processing`. Claimed, uploading,
checking, publishing, unknown, failed or stalled outcomes cannot start another
attempt through this admission. No automatic retry or new job fallback is provided.

After the Instagram job is confirmed published with its actual ID and permalink,
obtain the exact official CDN derivative and register a separate Threads review
with `--pair-id`. Use the Threads derivative bytes as its candidate and the delivery
binding described in CANDIDATE_REVIEW.md. The original SHA must equal the pair's
Slack source SHA; the paired Instagram job/post must equal the actual successful
first member. The live history may add ONLY that confirmed IG post. It must retain
all original three receipts unchanged. Threads' ordinal remains 3, and Instagram's
recent publication does not reset the Threads account's six-hour clock.

After Threads succeeds, both reviews have been consumed and neither member can
publish again. No future pair or recurring operation is enabled. Existing history,
legacy uncertainties, duplicate hashes, URL delivery verification, writer lock and
unknown-response quarantine remain in the same native ledger.

## Validation boundary

Tests exercise incomplete global inventory plus preserved known legacy unknowns,
native mock IG-second/Threads-third completion, default-path rejection, exact Slack/
source/body/account/ordinal binding, known matching attempts, new unknown/public
history, final uncertain publish responses, one-pair reuse and concurrent writer
rejection. Providers and uploads are mocks; no operational state is created.
Separate delivery tests cover real ffprobe and mock official CDN byte validation.

## Approved local format derivative for Instagram

The Slack source hash always remains the original attachment's hash. If an
approved format-only local derivative is needed, register ONE separate mapping
before either pair member has a review or job. This uses an append-only key in
the existing recovery_metadata table: no schema migration, marker edit, pair
rewrite or ledger recreation is required. A previous failed approval that stopped
before inserting a review/claim is compatible; actual existing reviews/jobs block.

Private derivation JSON:

```json
{
  "version": 1,
  "runtime_id": "EXISTING_RUNTIME_ID",
  "pair_id": "EXISTING_PAIR_ID",
  "account_id": "BOUND_INSTAGRAM_ACCOUNT",
  "job_id": "FIXED_INSTAGRAM_JOB",
  "source_sha256": "UNCHANGED_SLACK_ORIGINAL_SHA256",
  "derived_sha256": "APPROVED_LOCAL_UPLOAD_SHA256",
  "frame_count": 922,
  "frames_preserved": true,
  "audio_preserved": true,
  "transform_kind": "format_only",
  "approval_record": "USER_APPROVAL_RECORD_ID",
  "inspection_sha256": "SHA256_OF_PRIVATE_DERIVATION_REVIEW_REPORT"
}
```

The immutable report at artifacts-dir/<inspection_sha256> must explicitly bind
both actual hashes, describe the format transformation and retain frame-sequence
and same-music comparison evidence. The two true preservation flags are trusted
review findings, not conclusions drawn merely from matching frame counts.
Software recomputes both file hashes before/after inspection, checks the source
against the unchanged pair, counts actual decoded frames/audio tracks with ffprobe,
requires equal counts and at least one audio track, and runs native Reel profile
validation on the exact derivative. It does not claim that equal counts prove
identical pictures or music. It rejects arbitrary replacement, known attempts,
changed unresolved state, re-registration and changes after a review or claim.

```sh
python3 recovery/reconcile.py register-pair-derivation \
  --state-dir /private/EXISTING-runtime \
  --derivation /private/derivation.json \
  --source-file /private/slack-original.mp4 \
  --media-file /private/approved-format.mp4 \
  --artifacts-dir /private/evidence
```

This command performs local reads and one locked metadata append; it makes no
provider requests. The implementation session does not run it on operational
state. Do not reinitialize the existing pair or change its source hash.

Then register the Instagram candidate review using the derivative as --media-file;
candidate.media_sha256 and content_hash must describe those exact upload bytes.
Keep account, job, body, history and pair ID unchanged. Run the ordinary explicit
pair publish with that same local derivative file after review. Instagram's native
source receipt now identifies the uploaded local derivative. Subsequent Threads
delivery.source_sha256 must therefore equal this IG-upload derivative SHA, while
its remote_sha256 and local Threads file identify the separate official CDN
rendition. The immutable pair plus derivation record retain the full chain back
to the original Slack attachment without pretending the hashes are equal.
