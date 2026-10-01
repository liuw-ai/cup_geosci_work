# Phase 125: CMGB renderer recovery and targeted replay

## Completed

- Added a bounded CMGB detail-retry session recovery path. When a detail
  attempt reports an explicit renderer/runtime failure such as `Target
  crashed`, `Target closed`, or `Execution context was destroyed`, the worker
  closes the isolated context, reconnects to the dedicated CDP browser, and
  retries the same official detail URL. Access-policy responses (robots,
  401/403/412/429) remain non-retryable.
- Kept the frozen partial-capture contract unchanged: a retry is still limited
  to the failed URLs from one completed pagination pass, and a successful
  canonical manifest is written only after every discovered detail succeeds.
- Added source-level configuration for the recovery behavior and regression
  coverage for runtime-failure classification.

## Server verification (2026-10-02)

- The server could not fetch the new GitHub branch within 25 seconds. The
  reviewed commit `db4892d` was therefore delivered through the existing SSH
  channel into an isolated release directory
  `/home/ubuntu/cup_geosci_work_phase125_release`; the existing production
  worktree and database volume were not overwritten.
- Rebuilt and replaced only `cmgb-browser`; the web, primary worker, and other
  browser workers were not restarted.
- CMGB targeted replay completed `146/146` official detail pages with
  `detail_failed=0`, pagination complete, and no failure records.
- Official source sync consumed 146 rows and found 36 explicit student-scope
  matches. It created or updated no duplicate records; the public count
  remains 152 open student jobs.
- Database audit: 1,857 records checked, 0 audit issues. Internal readiness is
  true. Public readiness remains false solely because the formal domain/DNS
  check is intentionally not configured.

## Not claimed

This phase does not add historical or inferred jobs and does not treat the
36 CMGB matches as new vacancies. It only restores reliable capture of the
already discovered official rows. CNPC maintenance and Sinopec robots 403
remain explicitly access-limited; they are not reported as empty sources.

## Next gate

1. Add one independent, current Three-Barrel-Oil subsidiary source with a
   job-level official detail URL and two successful server refreshes.
2. Add one current provincial public-institution official position table and
   verify it twice before publishing any rows.
3. Revalidate the China Earthquake Agency official XLSX after its announced
   opening date (`2026-10-10`) before activating its 91 verified rows.
4. Keep the domain/DNS item separate; it is the only remaining public-readiness
   blocker and is not a data-quality substitute.
