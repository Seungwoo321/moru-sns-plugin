# V1 recovery: evidence quarantine, draft integration

When the original durable ledger cannot be mounted, an owned-post list is useful
reconciliation evidence, **not a complete publication-attempt journal**. Graph
cannot show a failed request or prove that an interrupted container will never
publish. A CDN rendition also cannot establish the original uploaded file hash.

This independent local tool imports only API-confirmed IDs, publication times,
permalinks, media types and text digests into `recovery.sqlite3`. It does not copy
or alter an old ledger, write `runtime.json`, create `publisher.sqlite3`, change
installed plugin files or enable any publisher. It is outside the plugin payload.
The version is the v1 recovery evidence schema, not a v2 runtime or deployment.

## Implemented

- Explicit new destination outside Git/plugin trees; same private-path/owner/mode
  restrictions as v1. Existing destinations, including partial runs, are rejected.
- Nonblocking parent-directory flock serializes cooperating initialization processes.
  The destination mkdir and file creation are exclusive. Source state is never opened.
- Fixed Graph endpoints, verified account ID and username, bounded cursor pagination;
  returned next URLs are never followed. Truncation, repeated cursors and missing
  receipt fields fail before state creation. No token or media URL is persisted.
- Source runtime and approved-record identifier, exact normalized manifest digest,
  account bindings and query time form explicit provenance. Completion is committed
  with imported evidence in one transaction, followed by fsync.
- Historical job references matching the selected account's official list are labeled
  `confirmed_post_reference`, never synthetic `published` engine receipts. Missing
  references remain `quarantined`; missing original hashes stay null.
- Inspection always reports `publish_ready: false`. A fresh job ID or retry cannot
  activate a publisher; the reservation API currently rejects all calls.

## Explicit manifest (private 0600 file, never committed)

Fields: `version: 1`, `source_runtime`, `source_record`, `inventory_complete`
(boolean), `old_writers_stopped` (boolean), `attempts` (list). Each attempt has
`platform`, `job_id`, `post_id` (nullable), `content_hash` (nullable original engine
hash), `media_sha256` (nullable original media hash). Identifiers must be bounded
plain identifiers; no credentials, URLs, captions or arbitrary notes are accepted.
An empty list does **not** prove the old history had no unresolved attempts.

```
python3 recovery/reconcile.py initialize --state-dir /private/reviewed-recovery \
  --manifest /private/recovery-manifest.json --connection threads --connection instagram
python3 recovery/reconcile.py inspect --state-dir /private/reviewed-recovery
```

Only use a verified private directory owned by the current user. These paths are
placeholders. No command automatically creates a parent or relaxes its permissions.
Do not run against the original ledger or manufacture original content hashes.

## Remaining gate before publisher integration

This draft is deliberately not a usable recovered publisher. A subsequent reviewed
change must implement and test all of the following before activation:

1. Establish exclusive ownership of the account, stop every previous writer, and
   obtain the full attempt inventory. An attestation is an input to review, not proof.
2. Resolve each unknown/publishing/uploading attempt from official container/post
   evidence or leave it quarantined. Missing records must never become completed.
3. Match original text, source/media identity and job IDs where available. Maintain
   separate provenance for API metadata versus operator-supplied hashes; missing
   hashes must continue to block affected replay and ambiguous candidates.
4. Under a single account-wide lock, refresh official owned-post history immediately
   before claiming work. Atomically claim unique job/content/media identities; retain
   failures and unknown outcomes. A new job ID cannot evade a prior content attempt.
5. Use verified publication time for the minimum six-hour interval; preserve existing
   engine receipt timestamps separately. Re-query before any uncertain retry.
6. Integrate the new store through a reviewed guard/schema migration. Never generate
   a fake legacy marker or inject imported rows into the legacy `jobs` table.
7. Fault-injection tests must cover commit/fsync interruption, every network boundary,
   concurrent claim workers, post verification, account swaps and restore provenance.

## Validation and host limitation

`python3 -B -m unittest discover -s recovery -p 'test_*.py' -v`

Tests exercise API pagination, manifest restrictions, the real private-path guard,
quarantine, repeated initialization, wrong-account refusal, secret exclusion,
provenance corruption and cross-process locking. On the current managed host both
`/workspace/.git` and `/tmp/.git` exist. The unmodified v1 guard intentionally rejects
state beneath those Git roots. Ten filesystem integration cases therefore skip
with an explicit reason; no monkeypatch, new private directory outside approved
roots, or relaxed guard is used. Run them in CI with a genuinely non-Git private
temporary directory before considering this importer validated for production.

No real account data, secrets, media, original ledger or operational recovery store
is part of this PR. No real publication or schedule mutation is performed by tests.
