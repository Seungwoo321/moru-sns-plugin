# Historical media gap: release blocker and narrow proposal

Status: design only, not an implemented publication exception. Keep PR #7 draft.

## Confirmed defect

Migration deliberately discards unverified manifest media/content hashes. Every
imported video therefore has NULL identities. The native claim query is global,
so one existing Instagram VIDEO blocks both Instagram and Threads indefinitely.
The native format acceptance tests do not establish usability for these accounts.
The regression test reproduces this with two accounts and a claimed historical
hash; neither platform obtains a claim. Changing NULL to a fabricated digest or
merely adding a platform filter would not resolve the media comparison problem.

## What is and is not established

The supplied new reel and still passed byte-size and SHA-256 verification. This
establishes transfer integrity only. No available evidence in this task links an
old original file's bytes to a particular verified old publication receipt.
Do not interpret the new file hashes as old original hashes. A CDN rendition's
different digest, dimensions, duration, or perceptual hash does not prove new
material: recompression, edits and crops can change all of them.

Two legitimate evidence routes exist:

1. Original recovery: obtain the old original through an approved private transfer,
   plus an existing attempt record binding that exact digest to a provider container
   and a verified account/post receipt. Recompute the digest, verify the chain and
   retain source provenance. A bare operator-supplied hash is insufficient. This
   still needs a content-reuse comparison; unequal byte hashes alone are not enough.
2. Candidate-specific material review: leave old original identities NULL. Review
   the full available public media and text for every historical receipt against
   the exact approved candidate. Record affirmative, concrete differences in the
   depicted development event and footage, not a numeric similarity threshold.
   Missing, ambiguous, partially available or overlapping material stays blocked.
   This is a reviewed editorial distinction, not mathematical original-byte proof.
   If that standard is insufficient, only route 1 with additional review is viable.

## Proposed bounded implementation, conditional on that evidence

Use a separate immutable candidate-review record, never a fake old receipt. Bind
it to runtime/account, platform, job ID, exact source SHA-256, computed content hash,
source event identity, full text digest, media kind, reviewer/source record and the
complete ordered history snapshot digest (IDs, links, times and comparison-artifact
digests). Retain private artifact references, never signed CDN URLs or credentials.
Every historical item must have an explicit reviewed comparison outcome.

The native transaction could discharge only the historical-identity-unknown gate
for this exact candidate if all comparisons are verified distinct. It must not
discharge unknown attempts, incomplete inventory, writer ownership, account binding,
six-hour limits, duplicate claims, or public-history drift. Any candidate edit,
new history item, changed link or unavailable evidence invalidates the review.
Reserve the exact candidate and consume its review with the ordinary job claim;
unknown provider results remain quarantined without reissuing a publication POST.

For the authorized Instagram-then-Threads sequence, explicitly bind two jobs to
one approved source bundle. After Instagram succeeds, include its verified receipt
in a new Threads history snapshot as an explicitly authorized cross-platform reuse,
never as a "distinct" comparison. Same-platform reuse remains blocked. Do not
generalize this authorization to other media, jobs, accounts or future runs.

All writers must still use one shared persistent ledger with old writers stopped.
A review record is not remote executor fencing and cannot authorize another copy.

## Required acceptance tests before implementation can be released

- Existing VIDEO with NULL original hash plus no review: both candidates blocked
  (implemented regression); supplied historical hashes alone do not unlock it.
- Exact reviewed distinct candidate: one claim only; old NULL fields stay NULL.
- Missing historical item, partial media, ambiguous difference or mere unequal
  SHA/perceptual hashes: no claim and no publication request.
- Altered file, text, job, account, source event or history digest: review rejected.
- Concurrent claims or reused review: at most one winner on the shared ledger.
- Unknown old or new provider attempt: blocked despite otherwise valid review.
- Fresh public link/time drift and account-scoped six-hour checks remain mandatory.
- Instagram success updates the Threads snapshot; only the explicit paired job
  accepts that cross-platform reuse. Other jobs and same-platform reuse fail.
- Stopped-writer/inventory gates and old-executor rejection remain unchanged.

## Next required facts

Supply either the old original-to-receipt chain, or complete inspectable old public
media with receipt bindings and a recorded comparison against the exact approved
candidate. The available new-file transfer proof alone cannot answer this. No
publication exception should be enabled before those facts and the review standard
are established. No operational API call is required to test the proposed gates.
