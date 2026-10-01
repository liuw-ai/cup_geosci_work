# Phase 121: immutable government refresh evidence

## Why this phase exists

The government-position publication gate already rechecked the official notice
and attachment URLs, but the database kept only the latest verification row.
That made it impossible to prove the acceptance requirement of two successful
refreshes for a provincial official table. A static registry timestamp is not
equivalent to repeated live checks.

## What changed

- Added `government_source_verification_events`, an append-only event table.
- Every automated verified, unavailable, withdrawn, or unconfigured check is
  recorded before the latest-state projection is updated.
- Added database queries for per-source total and successful refresh counts.
- Added `source_refresh_gate` to the CLI and daily government quality report;
  a source passes this gate only after two real `verified` events.
- Added regression coverage for event ordering, count semantics, and the
  two-success threshold.

This change does not create or activate a job. Existing historical checks are
not backfilled, so the first two successful events for each source must be
produced by real server worker cycles after deployment.

## Verification

- Local full test suite: `476 passed, 2 skipped`.
- The production database schema is created by the existing idempotent
  `Database.initialize()` path; no data migration or job rewrite is needed.
- Server deployment on 2026-10-02 completed from commit `bff58c6` with a
  verified SQLite backup. Web and worker are healthy; the production audit
  reports `1856` records and `152` student-visible open jobs.
- Two real official-evidence refreshes have now completed for six current
  government sources: Anhui, Gansu, Hubei, Ningxia, Hunan and China
  Geological Survey. The CLI reports `passed_sources: 6`; the China Earthquake
  Agency remains scheduled for its official 2026-10-10 opening date.

## Next acceptance work

1. Keep the worker's normal three-hour cycle running and retain the refresh
   event history as the acceptance evidence.
2. On 2026-10-10 re-fetch the China Earthquake Agency notice and attachment;
   activate only rows whose opening window is confirmed.
3. Continue the independent current Three-Barrel-Oil subsidiary detail chain.
