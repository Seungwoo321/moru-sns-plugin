# V1 recovery and native runtime migration

For the explicitly authorized single pair with incomplete global inventory, see
[ONE_PAIR.md](ONE_PAIR.md). This separate manual admission does not change the
ordinary recovery or scheduled/bridge gates described below.

The exact-candidate media review path is now implemented. See
[CANDIDATE_REVIEW.md](CANDIDATE_REVIEW.md) for the private evidence contract,
GET-only snapshot/registration commands and unchanged operational prerequisites.

An owned-post list is reconciliation evidence, not a complete attempt journal.
Graph cannot prove that an interrupted publication will never finish, and CDN
renditions cannot establish original uploaded file hashes. This tool never turns
those unknowns into successful local jobs.

## Workflow

1. `initialize` queries configured accounts and complete bounded owned-post history
   into a separate `recovery.sqlite3` evidence store. It writes no publisher marker.
2. `inspect` reports evidence completeness and quarantine conditions.
3. `migrate` reads evidence in a read-only SQLite transaction and populates an
   exclusively created native v1 runtime. It writes the real v1 marker **last**,
   only after committed imported state and provenance, using the existing initializer.
   Existing destinations, even partial ones, are never overwritten or repaired.
4. The native v1 guard validates database inode/device and recovery provenance.
   The native publisher enforces additional recovery gates before claiming work.

This is a branch change to v1, not a v2 service or new infrastructure. The active
installed copy and the old operational ledger are not changed by this workflow.
No schedule is enabled. Deploy the reviewed v1 code before using migrated state;
the `1-recovered` marker discriminator makes older installed executors reject this
state, rather than silently ignoring the additional recovery gates. This is a v1
compatibility discriminator, not a v2 runtime.

## Private manifest

A private 0600 JSON file has exactly: `version: 1`, `source_runtime`,
`source_record`, `inventory_complete` (boolean), `old_writers_stopped` (boolean),
and `attempts` (list). Each attempt has `platform`, `job_id`, nullable `post_id`,
nullable `content_hash`, nullable `media_sha256`. These hashes are historical
claims, not proof of original bytes. Unknown fields, credentials and free-text
notes are rejected. Identifiers are bounded plain identifiers. An empty attempt
list does not establish a complete history; an authorized operator must obtain
and review the original attempt inventory and exclusive writer ownership.

Commands below use placeholders for approved private paths:

```
python3 recovery/reconcile.py initialize --state-dir /private/recovery-evidence \
  --manifest /private/recovery-manifest.json --connection threads --connection instagram
python3 recovery/reconcile.py inspect --state-dir /private/recovery-evidence
python3 recovery/reconcile.py migrate --evidence-dir /private/recovery-evidence \
  --state-dir /private/recovered-runtime --runtime-id reviewed-v1-runtime
```

Parents must exist, be owned by the current user and have mode 0700, outside Git
and plugin payloads. Symlinks and reused destinations are refused. No command
loosens permissions, creates a parent, or opens the original operational ledger.
The `migrated` result confirms native format and guard acceptance, not publishability.

## Receipt mapping and fail-closed gates

- API-confirmed IDs, publication times, permalinks, media types and text digests are
  retained. Only a text post's content hash can be derived automatically from API
  text. Media content/original file identity remains unknown, even when the manifest
  supplies a hash; no fabricated video receipt is made from a transformed CDN file.
- Exactly matched historical job IDs are retained. Otherwise an explicit
  `imported.<post-id>` record identifies an imported official publication, not an
  invented past local execution. Ambiguous mappings fail. Unmatched attempts become
  `unknown` and prevent new publication, including under another job ID.
- Source runtime/record, manifest digest, evidence snapshot digest and import/query
  times are bound through the marker to database provenance. Imported publication
  timestamps are retained for the six-hour gate; no invented import-time publication.
- Every migrated writer takes an exclusive, nonblocking database flock for its
  complete lifetime, including fresh history check, claim and network processing.
  Native direct CLI and bridge both use this Ledger. Initialization locks the parent.
- The publisher rechecks identity, public links, official timestamps and the complete owned-post ID set before claiming
  or resuming. Drift, missing posts, or a next page requires explicit reconciliation;
  a truncated page is never accepted. Original snapshot collection supports bounded
  cursor pagination without following server-selected URLs.
- SQLite BEGIN IMMEDIATE protects job/content/media claims. Failed content stays
  reserved. New IDs cannot evade a prior hash or unresolved attempt. Same completed
  jobs return the stored receipt; same due media-processing jobs may resume. Unknown
  outcomes never automatically retry. The lock remains held throughout.
- Incomplete inventory, unstopped old writers, unknown attempts, unverified historical
  media/content hashes, and the six-hour interval all block publication. Source media
  reconciliation is deliberately not fabricated: accounts with such history remain
  blocked unless a separately verified exact-candidate review satisfies only that
  historical-identity gate. Original unknown hashes remain NULL.

## Tests and private-path boundary

```
MORU_TEST_ROOT=/approved/private/test-root python3 -B -m unittest discover -s recovery -p 'test_*.py' -v
```

The root must genuinely be outside Git and owned 0700. Tests create isolated
subdirectories and remove them; they never use an operational ledger. Network
responses are mocks. A separate test constructs a Git-root fixture and proves
that the unchanged production path guard rejects it. Thus runtime integration
and the intentional Git-path rejection are tested separately without mocking
or weakening the guard.

Tests cover native guard/engine acceptance, mocked end-to-end publication, official
history drift, uncertain outcomes, duplicate IDs/content, six-hour timestamps,
unknown quarantine, missing media identity, concurrent writers, source preservation,
partial-initialization marker absence, provenance validation and API pagination.
A successful mocked publication is not a real SNS publication or account permission
proof. Imported media history with missing source identity remains blocked without an exact-candidate review.

## Deployment boundary

A local flock coordinates only writers sharing this exact filesystem and ledger inode.
It cannot fence another container holding a separate copy. Deployment must stop old
writers and route every writer to one shared persistent private ledger; the manifest
attestation records that decision but cannot prove remote shutdown. Do not activate
a second private copy or treat local tests as proof of cross-container fencing.
