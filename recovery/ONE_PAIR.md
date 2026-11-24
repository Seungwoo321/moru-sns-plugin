# Explicit one-pair v1 admission

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
    "slack_channel": "D0CDWG6PE4U",
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

Use the alternative approved DM `D0CJGTZ9BN3` if that is the actual source. The
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
