# Optional private Blob delivery in native Work

Default **off**. This is a native process adapter, not a server, Function, public
Slack link, deployment, store provisioner or schedule. It only accepts the final
MP4 already received through an operator-approved Slack report. It neither fetches
Slack nor records, edits or encodes media. It reuses the v0.1.9 operating-profile,
read-only MP4 and exact HTTPS byte verifier. Full encoder certification limits
from [video-preflight.md](video-preflight.md) still apply.

The separate `blob_publish.py` native entrypoint supports an existing standard
native Threads ledger and an existing recovered native ledger with the exact
version-3 profile admission described below. No ledger migration, reset or
recreation is required. Instagram Unified remains on its existing resumable path;
its completed rows are preserved. Remote/v2 execution is not expanded.

## Dependencies and private configuration

The bundled Work-process bridge uses the official `@vercel/blob` SDK pinned to
2.9.0 with its lockfile. Install that dependency into the adapter's `scripts/blob-sdk`
directory through the normal approved runtime setup (`npm ci --ignore-scripts`).
No installation, login, project linking or provisioning happens automatically.
A missing SDK capability blocks before an upload attempt receipt is created.
Node.js supporting `--use-system-ca` (Node 24 is the verified runtime) and POSIX
file descriptors/flock are required. Unsupported Node versions fail before SDK
execution; there is no insecure fallback.

`MORU_PRIVATE_BLOB_CONFIG` points to private JSON outside Git/plugin trees, with
exact fields `version:1`, `enabled:true`, `store_host`, `receipt_dir`, and
`approved_slack_channels` (an explicit list). `store_host` must identify the already
approved private Blob store, `<store>.private.blob.vercel-storage.com`. The receipt
directory must already exist, mode 0700. Configuration and approval files are 0600.
No actual account/store/Slack identifiers are embedded in this plugin.

Inject only the dedicated `MORU_BLOB_READ_WRITE_TOKEN` secret reference into the
Work environment. The bridge receives it through a restricted child environment;
it never receives Meta credentials, ambient Vercel project/OIDC settings, token
arguments or an env file. A GET-only signed URL is checked against the configured
store hostname before a PUT or DELETE. No token/vault discovery or movement occurs.
The bridge requires the existing approved `HTTPS_PROXY` (or `https_proxy`) route.
Pinned Undici `ProxyAgent` explicitly carries SDK requests through that route;
`NO_PROXY` cannot select direct fallback. Missing/invalid proxy configuration blocks.
Normal TLS certificate/hostname validation stays enabled. The child uses official
Node `--use-system-ca` and inherits only existing `SSL_CERT_FILE`, `SSL_CERT_DIR`,
and `NODE_EXTRA_CA_CERTS` trust inputs. It does not copy certificates or change the
trust store; arbitrary `NODE_OPTIONS` and TLS-verification bypass variables are
not inherited. Destinations are limited
to the official `vercel.com/api/blob` API and Blob storage HTTPS hosts; no endpoint
or proxy is provisioned. The local proxy regression uses synthetic credentials and
a rejecting local CONNECT server, not a real store or a live success probe.

The ordinary fixed `THREADS_EXPECTED_USER_ID` / `THREADS_USER_ACCESS_TOKEN` and
existing ledger account binding remain required.

The private `--approval` JSON has exactly:

```text
version:1, platform:"threads", account_id, job_id:"capture-<final MP4 SHA256>",
media_sha256, content_sha256, slack_channel, slack_file_id, slack_message_ts,
approval_record, final_original:true, ai_generated:<explicit boolean>
```

`content_sha256` is native `content_hash(caption, video)` including the media's AI
choice. This is an operator assertion binding the already-reviewed Slack source;
it is not an automatic Slack authorization or visual-content classifier. Any
configured publication profile still requires its source/evidence/reviewed-hash
arguments and is checked before an upload.

## Native commands and state

```text
blob_publish.py plan --state-dir <existing-native-runtime>
  --approval <private-review.json> --video-file <final.mp4> --text-file <caption.txt>

blob_publish.py publish --state-dir <same-existing-runtime>
  --approval <same-review.json> --video-file <same-final.mp4> --text-file <same-caption.txt>
  --execute
```

For a configured profile, pass `--source-file`, `--publication-evidence` and
`--reviewed-hash` exactly as in the standard native publisher. Plan performs no
provider calls or state writes and never marks delivery/publication ready.

Each runtime/account/job is tied to one opaque object pathname and one private
receipt. A per-job flock and fsynced `preparing` record precede signing. Only this state
allows preparation retry: no PUT could have been dispatched. Immediately before
PUT, a separate fsynced `upload_started` fence makes any interruption uncertain.
Pre-PUT errors retain only the safe `pre_put_preparation_failed` classification.
Existing v0.1.10 uncertain records are never automatically reset or resent;
a not-found read alone is insufficient. Only the explicit, archived absent
continuation below can authorize one further same-path attempt. The SDK PUT
uses private access, the exact inherited read-only file bytes, video/mp4 MIME,
no overwrite and no random suffix. It verifies the hash before PUT. A lost PUT
response leaves the object/source/receipt unresolved and blocks a second upload.
SDK-internal transport retries cannot create alternate object names or overwrite
an existing object; ordinary publish never repeats an uncertain PUT.

The bridge issues only a **GET** delegation for that pathname, expiring after 15
minutes, then creates a private signed URL. The GET delegation expires 60 seconds
after that URL so the pinned SDK explicitly signs the shorter URL expiry. The native verifier checks the actual
served size, MIME and SHA with existing TLS/DNS/no-redirect controls. The signed URL
exists only in memory/private subprocess pipes; receipts store only its digest and
expiry. No signed URL or provider error text is printed. Publication uses the same
native publisher, exact approval and final pre-container expiry check. A transfer
success is not a published post.

Reinvoke only the same approved command to resume a recorded pending container.
It does not PUT again or create a new container. Unknown publication responses
permit only owned-history GET evidence and retain the object; the adapter does not
guess a lost post ID. Known confirmation states retain the publisher's GET-only
receipt confirmation. A newly approved different job is not a retry of this job.

## Recover an uncertain upload without uploading again

Use the same arguments with `blob_publish.py recover` (no `--execute`) for an
`upload_started`, `upload_unknown` or `verification_unknown` receipt. Recovery is
explicit and reads only: sign the exact immutable pathname, HEAD that object,
verify its signed GET bytes/SHA/size/MIME, then HEAD again and require unchanged
metadata and ETag. An already saved ETag must also match. Only then is that ETag
saved with `object_recovered`; this command makes no new PUT or SNS POST. A missing
object, changed generation, bytes, metadata or an uncertain read remains held.
After successful recovery, the separately approved `publish` command still runs
all normal candidate checks before its first container request. Any existing
unconfirmed SNS attempt stays in its native read-only reconciliation flow.

A confirmed published row resumes only exact receipt cleanup, even if unrelated
journal rows changed after publication. It does not reload new-candidate recovery
admission. Fixed account, content/approval/store/path bindings, exact official post
GET and conditional ETag deletion remain mandatory.

## Existing recovered ledger: exact Threads admission

Keep the same runtime and publication profile. First obtain a read-only snapshot:

```text
profile_recovery.py --state-dir <existing-recovered-runtime> --snapshot
  --platform threads --job-id capture-<final-SHA>
  --external-absences <private-observations.json>
```

The observations file has one `external_absences` array using the exact receipt
fields in [profile-recovery.md](profile-recovery.md). A user-confirmed app deletion
requires complete owned-history queries bracketing an individual official GET;
an inaccessible object alone is not enough. A one-time immediate replacement sets
`immediate_replacement:true` on exactly that Threads receipt. It is reserved for
this candidate and cannot be reused. Subsequent ordinary candidates may carry the
same historical absence as `false`; that observation does not waive any interval.
An empty array is allowed when all saved receipts remain present.

Prepare the same private profile admission document/artifacts as the existing
recovery flow, with `version:3`, `platform:"threads"`, the current snapshot, exact
source/content/profile hashes, preserved pending exclusions and an additional:

```text
transport: {
  kind: "vercel_private_blob",
  store_host: <configured private hostname>,
  config_sha256: <SHA256 of candidate_review.canonical(parsed config) UTF-8>,
  approval_sha256: <SHA256 of candidate_review.canonical(parsed Blob approval) UTF-8>
}
```

Register using `profile_recovery.py --state-dir ... --platform threads --review ...
--source-file ... --video-file ... --text-file ... --publication-evidence ...
--artifacts-dir ... --blob-approval <same-private-review.json>`. This appends only
admission/reservation metadata. Then run the `blob_publish.py` commands above with
the matching source/evidence/reviewed hash. The profile, account, original final
bytes, exact approval and private store configuration must all match. Admission is
revalidated before upload and again at native claim. Live history changes,
additional unresolved attempts, changed old rows and duplicate source claims
block. Existing unknown attempts remain unknown; inventory completeness is never
asserted. Each later source needs its own fresh admission and its own temporary
object, with the ordinary account interval and single-writer guards intact.

## Expiry, confirmation and actual cleanup

URL TTL limits access; it is **not** automatic deletion of the Blob. No object TTL
is set before confirmation, because an unresolved provider outcome must preserve
the source and exact private copy. No cleanup task is automatically scheduled.

After the journal records a published job, the adapter independently checks the
fixed account and GETs its exact post ID, VIDEO type and saved permalink. Only then
it persists `cleanup_pending` and performs SDK `head` + `del(...,{ifMatch:etag})` on
that exact pathname. A following HEAD must confirm absence. It preserves the local
source, publication receipt and transfer audit record. It never touches another
Blob, the Slack source or an existing user's object.

Deletion timeout/failure keeps `cleanup_pending`. Reinvoking the same command after
restart performs official receipt GET confirmation and the same conditional
cleanup only; it never republishes. If the object is already absent, cleanup is
complete. An accepted deletion whose following HEAD is still cached or unavailable
is saved as `cleanup_verify_pending`. Reentry performs HEAD-only verification,
not a second DELETE; cache propagation is distinct from a failed delete request.
Changed ETag, missing/mismatched post, ambiguous response, wrong store or
unsupported recovery admission holds safely. The same native entrypoint is the
explicit later cleanup mechanism; do not enable schedules as part of setup.

## Verified scope

Tests use synthetic MP4s, synthetic private native ledgers, mocked Meta/HTTPS and a
mock SDK. They exercise the actual native CLI and the SDK bridge's call contract.
No operational Blob, vault, Meta account, journal, host or schedule was changed.
The SDK's published exports/types were checked; real Meta fetching of a Vercel
GET-only signed URL has **not** been established. A provider might require a
longer window or another operation; do not silently broaden the delegation.

Sources: [Vercel signing announcement](https://vercel.com/changelog/vercel-cli-now-supports-signing-blob-urls),
[Signed URL SDK](https://vercel.com/docs/vercel-blob/vercel-signed-urls),
[Blob SDK](https://vercel.com/docs/vercel-blob/using-blob-sdk).


## Explicit same-object absent continuation

`blob_publish.py recover-absent --execute` takes the same state/approval/video/text
and profile arguments as `publish`. This command is restricted to an existing
recovered Threads publication-profile ledger with exact version-3 admission.
Standard ledgers are unsupported, even with a configured profile. Continuation
publish entry and the immediate pre-PUT boundary revalidate that same scope and
the existing base/bound-content recovery checks. It performs **zero PUT and zero Meta writes**.
Only an `upload_started`/`upload_unknown` receipt without known object ETag, provider
IDs or a previous continuation is eligible. The exact candidate must have no native
Meta job/container and no same-content claim. The job flock and SQLite write lock
remain held throughout the check. It requires a fresh official GET delegation/signature
for the same private store/path, followed by an authenticated SDK `list` with that
exact prefix and explicit complete bounded pagination. Missing pagination fields,
cycles, authentication errors, any returned object (even a prefix collision), or
ambiguity block without changing the receipt. A not-found exception/dashboard count
alone never authorizes continuation.

After proof, the original receipt bytes are fsynced to an exclusive
`<receipt>.prior-<SHA256>.json` archive. A linked `preparing` continuation records
that digest and observation time. Repeating `recover-absent` cannot authorize a
second continuation. Then invoke the separately authorized normal `publish` command:
it verifies the archive digest, all normal candidate/live-history guards and a new
valid signature, fsyncs the PUT fence, and uses the same immutable pathname with
`allowOverwrite:false`. A late prior PUT or any existing object cannot be overwritten.
If this continuation becomes uncertain, no further absent retry is allowed; use
same-object read-only reconciliation. Old jobs/receipts are never deleted.

The official JS SDK is retained now that managed CA/proxy signing is available;
Python 0.5.0 lacks GET-signing and ETag-conditional deletion. No provider switch,
new host, or alternative endpoint is introduced. These paths are verified with
synthetic ledgers and mocked official API responses, not live operations.

Trust configuration references: [Node CLI CA settings](https://nodejs.org/api/cli.html#--use-system-ca),
[Undici ProxyAgent](https://undici.nodejs.org/api/ProxyAgent).
