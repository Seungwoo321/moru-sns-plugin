# Native recovered-journal profile candidates

This is a new-candidate admission, not a new ledger, migration, retry or edit of
an old pair. Only Instagram Unified with local resumable video is implemented.
Use the same approved private native runtime and existing injected credentials.
The profile, capture evidence and `capture-<source SHA256>` job rules are described
in [publication-profile.md](publication-profile.md).

1. Prepare the actual approved original, English review evidence and final
   30–150 second MP4. Run native `publisher.py plan` with the same profile,
   source/evidence, caption, `--instagram-login-mode unified` and
   `--instagram-upload-mode resumable`. Keep its exact `content_sha256`.
2. Read the existing journal and complete official account history:

   ```text
   profile_recovery.py --state-dir /private/EXISTING-runtime
     --snapshot --platform instagram --job-id capture-<full source SHA256>
   ```

   This makes official GETs, holds the existing journal lock and writes no state.
   It supplies runtime/account/job, history and prior-jobs/legacy-attempt digests.
   Missing public posts still block unless the exact version-2 app-deletion observation below is supplied; deletion is never inferred silently.
3. Build a private review JSON with exactly:
   `version:1`, `runtime_id`, `platform`, `account_id`, `job_id`,
   `source_sha256`, `media_sha256`, `profile_binding_sha256`, `content_hash`,
   `base_content_hash`, `history`, `prior_jobs_sha256`, `legacy_attempts_sha256`,
   `candidate_never_attempted:true`, `single_writer_confirmed:true`,
   `approval_record`, `distinctness_report_sha256`, `pending_exclusions`.
   `profile_binding_sha256` comes from the plan profile result; `content_hash` is
   the plan hash; `base_content_hash` is native `content_hash(text, media)` without
   the profile binding. Use the actual profile disclosure on the media.
4. The distinctness report must document actual comparison with all old public
   media and attempts. Each specifically excluded unrelated legacy `unknown`
   needs `{platform,account_id,job_id,report_sha256}` in `pending_exclusions` and
   a real report file named by its SHA in the private artifacts directory.
   Active processing/publishing attempts cannot be excluded. Never fabricate
   these findings; hashes only bind the operator's review, not prove its truth.
5. Register the exact new approval without any SNS POST:

   ```text
   profile_recovery.py --state-dir /private/EXISTING-runtime
     --review /private/native-review.json --source-file /private/original.mp4
     --video-file /private/final.mp4 --text-file /private/caption.txt
     --publication-evidence /private/capture-review.json
     --artifacts-dir /private/review-artifacts
   ```

   This adds one immutable record to existing `recovery_metadata`. It does not
   change the marker, schema, old jobs, old pair, provenance or completeness.
6. Publish only in the separately authorized operator session using the exact
   native plan arguments and `--reviewed-hash`, without `--candidate-pair-id`.
   Under the same writer lock and `BEGIN IMMEDIATE`, claim rechecks the approval,
   prior jobs, failed/unknown attempts, source/media claims and per-account six-hour
   interval. Original and final SHA claims are added atomically with the new job.
   A claim cannot overwrite a prior source. Existing unrelated unknowns remain
   unknown. Changing any prior job/history invalidates the new approval.
7. Processing resumes the same container. A received publish ID becomes
   `confirming_label`; Unified GET must confirm ID, permalink, VIDEO,
   `media_product_type=REELS` and the exact AI boolean before success. Unknown
   label outcomes permit only GET confirmation, with the recorded login/upload
   mode fixed. Completed/failed/unknown jobs never trigger another POST.

No general IMAGE or Threads VIDEO delivery proof registration is added here.
Those platforms are held before a claim; do not create hosting or downgrade their
media. Remote evidence transport remains unsupported. App-side deletion observations use only the exact candidate-bound path below.
No deletion API, historical receipt deletion or automatic replacement is implemented.

Legacy pair creation (a separate older path) reads `MORU_APPROVED_SLACK_CHANNELS`
as an explicit private JSON array of allowed DM IDs. No source IDs are embedded
in the distributed plugin. Missing configuration blocks new pair registration;
existing recorded pairs and this native profile approval are not rewritten.


## User-confirmed app deletion: one exact native replacement

This additive version-2 approval supports Instagram Unified/resumable only. It is
not a general interval override. The user must have explicitly confirmed app-side
deletion and immediate replacement of the exact target receipt with this candidate.
A Graph unsupported-object response alone does **not** prove deletion: lost access
can produce the same response. The code requires the user confirmation, the saved
published receipt, verified account ownership, complete paginated account lists
before and after the individual object GET, and the exact missing-receipt set.
It accepts only HTTP 400 / Graph code 100 / subcode 33 for that GET; authentication,
permission, rate-limit, transport, malformed and other ambiguous errors block.
No provider response text or credential is persisted.

Use the same approval fields above with `version:2` and `external_absences`, an
array of exact records with these fields:

```text
platform, account_id, job_id, post_id, permalink,
user_confirmation_record, report_sha256, immediate_replacement
```

The first five values must equal a saved `published` job. `report_sha256` names a
private content-addressed report in the same artifacts directory, documenting the
user's deletion/one-candidate replacement authorization and actual observations.
Exactly one record must have `immediate_replacement:true`, on the new candidate's
platform/account. Only that exact saved job is excluded from this one candidate's
interval check. Other missing receipts, including another platform's deleted post,
may be listed with `false` solely to reconcile history; they never grant another
platform a publication or interval exception. All still-public history must match,
and all other prior source/content, unresolved-attempt and six-hour guards remain.
No unknown attempt can be presented as a deleted published receipt.

To obtain the initial snapshot, put `{ "external_absences": [...] }` in a private
JSON file and add `--external-absences /private/absence-review.json` to the snapshot
command. This is GET-only and writes nothing. Copy the returned history/digests
into the exact version-2 candidate approval. Registration uses the existing
`--review` command and redoes the complete checks. It appends the immutable review
and a unique target-receipt reservation in the same transaction. Registration
consumes that receipt's replacement authorization even if the candidate later
fails: it cannot be moved to another job or used to retry an uncertain POST.

Before a new claim and each processing resume, official history and the individual
absence are rechecked. The database lock remains held; the claim transaction
revalidates all prior jobs and applies the one-receipt exception, never changing
its `published` row, timestamp, permalink, content/media claims or metrics. Once a
publish ID is saved, `confirming_label` stays GET-only as before; a lost response
without a known ID stays `unknown`. No schema, global completeness flag, other
approval, schedule, old receipt or old tag is changed.

This does not authorize further replacements or solve Threads media delivery.
A later candidate needs its own explicit review; it cannot reuse this reservation.
The current observation matcher intentionally does not automatically inherit
absence exemptions from earlier approvals.
