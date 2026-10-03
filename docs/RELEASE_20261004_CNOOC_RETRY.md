# Release 2026-10-04: CNOOC Detail Retry

## Release identity

- Git commit: `9421638`
- Branch: `release/20261004-cnooc-retry`
- Application image: `cupb-geoscience-job-hub:release-9421638`
- Browser image: `cupb-geoscience-job-hub-browser:release-9421638`
- Application image ID: `sha256:b8b237fb64f4438c69d1375fd11ffefac228f0af7a08f1d976ace7e35ffa451b`
- Browser image ID: `sha256:7114aa39472edb564c6539931fcf7c83f8a6b4e7118f8fdd787dbfc1e4b86597`

## Change

CNOOC detail capture retries a transient missing `window.__INITIAL_DATA__`
response once in a fresh browser page. A detail that still fails remains in the
partial-failure record, so the complete-scan publication gate is unchanged.

## Verification

- `611 passed, 2 skipped`
- `python -m compileall -q job_hub`
- local `release-check`: passed
- production `/version`: reports `release-9421638`
- production `release-check`: passed
- production Web, Worker and six browser workers: `release-9421638`
- database backup: integrity check passed; independent copy reports 2,266 jobs

## Data gate

The production database remains at 111 current domestic public jobs. The 1,000
job target, source concentration, provincial public-institution coverage and
current civil-service tables remain open gates. Candidate, historical,
access-limited and pending-evidence rows are not counted as public jobs.

## Runtime notes

- CNPC capture is scheduled after the official 00:00-06:00 maintenance window.
- Sinopec and PipeChina remain access-limited when their official robots checks
  return 403 or TLS errors; no historical snapshot is promoted as current.
