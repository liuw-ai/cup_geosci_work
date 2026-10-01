# Phase 110: Offline Browser Overlay Deployment

## Deployment

The normal browser image build was blocked by a slow Playwright wheel download.
The existing project already contained a safer fallback for source-only
releases: build `deploy/Dockerfile.browser-overlay` from the previously
validated browser image and replace only `/app/job_hub`. This phase added the
Compose override `deploy/docker-compose.phase109-browser-overlay.yml` and
deployed it on 2026-10-01.

All four isolated browser workers now use:

```text
cupb-geoscience-job-hub-browser:phase109-overlay
```

The web/worker containers, SQLite volume, official ledgers and public data
mounts were not replaced.

## Runtime results

- CMGB/国聘 detail retry: 8 pages, 146 rows discovered, 146 details
  succeeded, pagination complete; no new current row was created.
- CNPC browser: HTTP 400 from the official listing endpoint; retained as
  access-limited and not treated as an empty source.
- Sinopec browser: direct transport is active, but the server cannot verify
  the target certificate chain while reading `robots.txt`; capture remains
  blocked and TLS verification was not disabled.
- CNOOC browser: worker is running and remains in capture state; its last
  completed public API run still had no row with publishable detail evidence.

The production readiness report remains `internal_ready=false` because CNPC
and Sinopec browser workers are degraded. This is an operational failure, not
a vacancy conclusion.

## Province review

Server-side direct scans of the configured Shandong, Henan, Tianjin, Beijing,
Fujian, Hebei, Anhui, Jiangsu, Shanghai and Zhejiang sources completed without
producing a current student-eligible position row. Existing official evidence
was preserved; no historical table was imported.

## Acceptance

The project remains at approximately **72/100**. This phase removes the image
build blocker and confirms CMGB's detail chain, but does not add current jobs.
The 75-point gate still requires two provinces with current official tables
refreshed twice, a new Three-Barrel-Oil job-level chain, and the China
Earthquake Agency revalidation after 2026-10-10.
