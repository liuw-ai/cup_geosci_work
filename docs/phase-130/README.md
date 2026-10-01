# Phase 130: close the official-link and browser-evidence gate

## Completed

- Audited the student publication contract before expanding source coverage.
- Added `official_browser_capture_row` and
  `official_cnpc_browser_job_row` to the job-level evidence scopes. Complete
  browser captures now receive the same professional/degree gate as HTML
  detail rows and official attachment rows.
- Added deterministic official-link selection for public job detail pages:
  - row-bound detail URL when the capture provides one;
  - official position-table/notice attachment for attachment rows;
  - official evidence URL as the final evidence fallback.
  Application portals remain a separate link and are never used as evidence.
- Added a release-audit check that the persisted official evidence URL is on
  the registered source, detail, or attachment host allowlist.
- Added regressions for browser evidence publication, detail-versus-portal
  link selection, attachment link selection, and evidence-host mismatch.

## Verification

- Targeted evidence/profile/browser tests: `50 passed`.
- Full local suite: `489 passed, 2 skipped`.
- `python -m compileall -q job_hub`: passed.
- `git diff --check`: passed.
- No production database or server deployment was changed in this phase.

## Honest score

The score remains approximately **74/100** (excluding domain/DNS and
production deployment). This phase removes a publication/link correctness
defect; it is not counted as nationwide source expansion.

## Next gate

1. On/after `2026-10-10`, revalidate and activate only the China Earthquake
   Agency official position rows that pass the current official evidence,
   major, degree, location, opening-date, and deadline gates.
2. Complete one independent provincial official position-table adapter and
   prove two successful refreshes before making its rows student-visible.
3. Complete one Three-Barrel-Oil subsidiary detail adapter and prove two
   complete detail-level refreshes; partial or blocked scans must retain the
   prior snapshot and enter manual review.
4. Keep the annual civil-service table audit-only until the current official
   table is confirmed; historical tables must never be imported as current.
