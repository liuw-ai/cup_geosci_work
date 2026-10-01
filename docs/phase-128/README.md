# Phase 128: read-side protection for undated source windows

## Completed

- Re-audited the Phase 127 deadline gate and found one remaining lifecycle
  gap: explicit deadlines were protected on public reads, but sources using
  `undated_open_window_days` still relied entirely on the worker to mark old
  rows as expired.
- Added one shared SQL freshness predicate that applies both explicit
  `deadline_date` and the source's verified undated window from `sources.config_json`.
- Applied the predicate consistently to public lists, profile-filtered lists,
  details, categories, daily report changes, and `/healthz` counts.
- Kept internal/history queries unchanged when no `as_of_date` is supplied.
- Added a regression that simulates a worker outage after publication and
  proves an old undated row cannot remain student-visible.

## Verification

- Local regression suite: `482 passed, 2 skipped`.
- `python -m compileall -q job_hub`: passed.
- Flask application import and route registration: passed (`39` routes).
- No server deployment, database migration, source activation, or vacancy
  import was performed in this phase.

## Honest score

The breadth score remains approximately **74/100**. This phase closes a
realistic stale-publication failure mode but does not add an official source,
so the score is not inflated. Domain/DNS and production deployment remain
outside this score.

## Next gate

1. Complete one new provincial official position-table adapter and verify it
   with two successful server refreshes before publishing rows.
2. Complete one independent Three-Barrel-Oil subsidiary detail adapter and
   verify its detail-level fields twice on the server.
3. After `2026-10-10`, revalidate the China Earthquake Agency XLSX and activate
   only rows passing official evidence, major, degree, and deadline gates.
4. Keep annual civil-service rows audit-only until the current official table
   is confirmed; do not import historical positions as current vacancies.
