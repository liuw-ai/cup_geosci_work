# Phase 71: Browser Worker Runtime Recovery

## Objective

Make the existing official-browser capture path executable in production.
The CNPC and 国聘 workers are optional, read-only workers; they must not fail
because the ordinary web image intentionally omits Playwright.

## Delivered

- Added `Dockerfile.browser`, derived from the application image and installing
  `requirements-browser.txt` at image build time.
- Changed both `cnpc-browser` and `cmgb-browser` to build the dedicated browser
  image. The CDP browser remains an internal `headless-shell` service and is
  not exposed to the public network.
- Fixed both headless-shell services to bind CDP on `0.0.0.0:9222` inside the
  Compose network. No host `ports` mapping is added, so this endpoint remains
  inaccessible from the public internet.
- Removed the runtime-only wheel-cache dependency from the browser Compose
  file. `cnpc_browser_entrypoint` remains as a defensive fallback, but normal
  startup no longer needs PyPI or a writable Python package directory.
- Added a regression test that prevents the browser services from silently
  reverting to the ordinary image.

## Operational boundary

This phase fixes the worker runtime; it does not claim that CNPC has returned
岗位详情. A capture is publishable only after all official pages are scanned,
detail fields are complete, evidence URLs pass the CNPC allowlist, and the
existing student professional/degree/deadline gate succeeds. HTTP 412,
maintenance windows, robots restrictions, or partial detail scans remain
source failures rather than zero vacancies.

## Deployment

Build the normal image first, then the isolated browser services:

```bash
docker compose build web worker
docker compose -f docker-compose.browser.yml build cnpc-browser cmgb-browser
docker compose up -d
docker compose -f docker-compose.browser.yml up -d
```

Verify the worker's dependency and heartbeat before enabling any new source:

```bash
docker compose -f docker-compose.browser.yml exec -T cnpc-browser \
  python -c "import playwright; print(playwright.__version__)"
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 180
docker compose exec -T web python -m job_hub.cli audit
```

If the official platform rejects the request, the worker records the failure
capture and keeps the last valid snapshot. It never publishes an empty scan as
“无岗位”.
