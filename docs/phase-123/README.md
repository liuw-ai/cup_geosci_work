# Phase 123: separate Sinopec browser capture from normal sync

## Finding

The Sinopec SPA has a dedicated browser worker. Its current server state is
truthfully `robots.txt HTTP 403`, but the ordinary source synchronizer still
treated `sinopec-career` as an enabled snapshot source. That left a stale
`parse_failed` run in the production audit whenever the browser could not
refresh the SPA.

This was an orchestration error, not evidence that the 132 official units or
the 35 geoscience candidate units had no vacancies.

## Change

- `sinopec-career` remains registered and keeps the 132/35 capture contract,
  but is disabled for ordinary source synchronization.
- `sinopec-browser` remains the only producer for this dynamic source. Its
  `access_limited` heartbeat and adjacent failure capture remain visible to
  operations; no access restriction is converted into an empty result.
- Production readiness still monitors a disabled `browser_worker_only` source
  heartbeat, so disabling ordinary synchronization cannot hide a dead worker.
- The organization registry explains the worker-only runtime mode.
- No stale Sinopec snapshot was promoted and no new job was imported.

## Acceptance

After deployment and source bootstrap:

- the audit must no longer list the old Sinopec ordinary-sync failure as an
  enabled-source failure;
- `sinopec-browser` must remain visible as `degraded/access_limited` while the
  official site returns 403;
- the existing student count and evidence records must remain unchanged;
- once the official portal is reachable, the browser worker can refresh the
  current manifest without changing the normal source scheduler.
