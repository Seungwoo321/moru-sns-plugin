# Private publication profiles (v1)

Profiles are optional and operator-specific. No account, Slack source, secret or
user preference is embedded as a global plugin default. This implementation does
not install or alter an operational profile, journal, schedule or account.

Set `MORU_PUBLICATION_PROFILE` to an approved absolute private JSON file outside
Git/installed plugins (0700 parent, 0600 regular file, no symlinks). An example for
an operator who has approved English capture and AI disclosure is:

```json
{"version":1,"profile_id":"operator-english-capture","language":"en",
 "require_fresh_capture":true,"max_capture_age_hours":24,"ai_disclosure":true,
 "check_interval_hours":12,"minimum_publish_interval_hours":6}
```

The check interval is policy metadata; no schedule is created or changed. The
publication minimum is per platform/account, cannot be less than six hours, and
has no replacement/deletion exception. Capture age is a review setting, not proof
that a capture is new. Never treat the example as permission to publish.

For each candidate, provide `--source-file` and `--publication-evidence` pointing
to private original and review files. The evidence has exactly these fields:

- `version: 1`, matching `profile_id`, `source_kind: video|screenshot`.
- `source_sha256`, `media_sha256`, `caption_sha256`: exact original, final file and
  canonical approved caption hashes, computed from actual bytes.
- `title`, `ui_language`, `subtitle_language`, `title_language`, `caption_language`.
  Language fields must be `en`; absent subtitles may be `none`.
- `captured_at`: UTC epoch seconds; `fresh_capture`: explicit boolean finding.
- `review_record`: nonempty private reference to actual UI/subtitle/title/caption
  and capture review. Do not invent a record merely to pass validation.

Software checks these findings for consistency and hashes. It does not perform
OCR, prove the spoken language or prove capture freshness. ASCII text checking is
conservative and is not proof of English. A reviewed report is still required.

Use stable `--job-id capture-<full original SHA256>` for both platforms. Each
platform retains its own receipt. A new ID cannot reuse the same source. A changed
caption/profile/evidence changes the approval hash and cannot rebind an existing
job. Keep failed/unknown/completed jobs intact; do not rename or rewrite originals
to evade history. Historical source identity still requires the existing review.

Run the native CLI `plan` with the approved media/text, source/evidence and the
existing state directory. Then use its exact `content_sha256` as `--reviewed-hash`
for `publish --execute`. The profile and evidence digest, final media, caption
and disclosure are bound together; changes block before claim/container creation.
A profile requesting AI ON prepares media with that explicit decision. Ordinary
captures without such a profile are not automatically labeled AI generated.

For AI-required posts, save the returned POST ID in `confirming_label` before
validation. Instagram reads `is_ai_generated` through the official media GET;
Threads verifies the explicit container request plus actual post ID/type/link,
not an unimplemented AI-label readback. Uncertainty stays GET-only on the same
job. No second POST, metrics or published result is allowed before confirmation.

## Current release blockers

- General screenshot-only Threads IMAGE delivery is not implemented in v1.
  It is selected as IMAGE and held, never converted to video/text.
- v1 remote tools cannot carry this profile evidence and fail closed when a
  profile is configured. They do not silently use their text-only Threads route.
- Historical pair approvals are not rewritten. For a new Instagram Unified
  candidate on the existing recovered journal, use [native recovery admission](profile-recovery.md).
  It adds an exact profile approval and source claims in the existing storage.
  Existing v0.1.6 receipts and unresolved history remain intact. Threads and
  general IMAGE routes are still held when delivery proof is not registered.

These are explicit compatibility limits of this release. The reviewed native
Instagram Unified recovery admission is available as documented; this does not
claim complete two-platform delivery. No v2 routing or external hosting is introduced.

App-side deletions are a separate operation. Preserve existing native receipts;
a missing post or changed live history is not permission to erase a row, reset the
six-hour gate or replay its POST. The historical recovery reader continues to
block on history drift rather than invent a completed deletion or replacement.
No delete endpoint, replacement exception, hosting or public file route is added.

Profile account eligibility and new claim insertion share one `BEGIN IMMEDIATE` transaction. Different-source concurrent jobs cannot both pass the pending-job or six-hour gate. Existing same-job GET-only confirmation does not re-enter new publication admission. Instagram Unified (Facebook Login) additionally checks `media_product_type=REELS`; Instagram Login checks the explicit REELS request and VIDEO/AI media receipt without querying that mode-specific field.
