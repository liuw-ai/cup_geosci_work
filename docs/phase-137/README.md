# Phase 137: CMGB renderer stability and production acceptance

## Delivered

- Isolated the CMGB/国聘 browser worker from the other dynamic workers and
  diagnosed `Page.wait_for_selector: Target crashed` as a CDP/Chromium renderer
  failure. The dedicated CDP endpoint was returning HTTP 500 while CNPC,
  Sinopec and CNOOC remained healthy or explicitly access-limited.
- Increased the CMGB headless-shell shared memory from Docker's default 64 MB
  to 512 MB and added `--disable-dev-shm-usage`. Only the CMGB service was
  recreated; the other browser workers and the web/worker services were not
  restarted.
- Completed a fresh server capture on 2026-10-02: 8 pages, 146 discovered
  details, 146 exported rows, 0 failures, and pagination complete. The output
  contains a valid `capture_evidence` manifest (`adapter_version=cmgb-browser-v1`).
- Ran `sync-source cmgb-iguopin-browser`: 146 discovered, 36 open matches,
  0 created, 0 updated, 0 withdrawn, run `2152`.
- Created and verified `/home/ubuntu/backups/job_hub-phase136-cmgb-success-20261002.sqlite3`;
  SQLite `integrity_check` returned `ok` and the backup contained 1,857 jobs.

## Verification

- CMGB browser heartbeat: `running`; detail scan reports `detail_failed=0`.
- Production readiness: internal readiness `true`; the scorecard reported
  `81.55/100` with 152 open student-facing jobs.
- Local regression: `11 passed, 1 skipped` for the dynamic manifest and CMGB
  worker tests; Compose configuration and `git diff --check` passed.

## Still open

- CNPC and Sinopec remain explicitly access-limited (`HTTP 400` and `robots
  403`), so they must not be presented as empty sources.
- Current production readiness is not public-ready because the configured
  domain/HTTPS is intentionally out of scope and daily mail delivery remains
  `skipped` with `MAIL_ENABLED=false`.
- Broader source expansion (CNPC/Sinopec/CNOOC/pipechina subsidiaries,
  provincial tables, and the current official civil-service table) remains a
  separate data-expansion task; this phase does not invent rows to improve the
  score.
