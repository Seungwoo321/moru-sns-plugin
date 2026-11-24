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
   Missing public posts still block; an app deletion is not silently accepted.
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
media. Remote evidence transport remains unsupported. App-side deletions continue
to require separate receipt-observation reconciliation; no deletion API, historical
receipt deletion, automatic replacement or interval exception is implemented.
