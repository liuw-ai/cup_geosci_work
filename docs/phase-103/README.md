# Phase 103: Sinopec browser capture worker

## Purpose

This phase turns the previously manual Sinopec SPA snapshot into a repeatable
browser-worker path. The worker uses the official rendered page context and
the read-only endpoints exposed by the page:

- `selectYpList`: all enterprise pages;
- `selectPositionList`: each enterprise's position pages.

The capture contract requires complete enterprise pagination, a detail result
for every listed enterprise, explicit position title/major/degree/location/
headcount/deadline fields, and official Sinopec detail URLs. A partial or
access-limited run is written only as a failure diagnostic and cannot replace
the last successful capture.

## Implementation

- `job_hub/sinopec_browser_capture.py` contains the API-in-browser capture and
  fail-closed manifest validator.
- `job_hub/sinopec_browser_worker.py` runs it every three hours in an isolated
  Chromium service.
- `docker-compose.browser.yml` adds `sinopec-browser` and its headless-shell.
- The normal source adapter consumes the runtime capture only when it is fresh;
  the old checked-in snapshot is not used as a daily-refresh substitute.
- Readiness now includes the Sinopec browser heartbeat.

## Server verification on 2026-10-01

The server built and started the new container. The worker reached the
official source boundary, but `https://job.sinopec.com/robots.txt` returned
HTTP 403. It recorded `degraded / access-limited`, did not call the SPA data
endpoints, did not write a public capture, and did not publish any new rows.
This is the correct result under the project's source-admission policy; the
worker must not bypass robots restrictions merely because a browser can render
the page interactively.

The earlier read-only diagnostic confirmed that, when an authorized browser
session is allowed to inspect the page, the official list endpoint reports 132
units across seven pages and the unit detail endpoint exposes the required
fields. That diagnostic is not treated as a production capture because it did
not pass the robots gate.

## Verification

```text
pytest -q
451 passed, 1 skipped
```

The server backup created before deployment passed SQLite integrity and table
checks. Production readiness remains false because CNPC is access-limited,
Sinopec is access-limited, the national pipeline capture is absent, and the
public domain is not configured. Current objective score remains **71/100**:
the repeatable adapter and truthful failure state improve maintainability, but
no additional current official Sinopec rows can be counted until the official
access policy permits a compliant capture.

## Next gate

Do not relax the robots gate. The next qualifying step is either an official
read-only access path permitted by the publisher or a separately authorized
administrator capture imported through the existing complete-manifest review.
In parallel, continue current official provincial public-institution tables;
the national civil-service table remains zero until the annual official table
is published and verified.
