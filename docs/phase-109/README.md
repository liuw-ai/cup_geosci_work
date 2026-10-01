# Phase 109: Direct Source Review and Browser Transport Fix

## What was verified

On 2026-10-01 the production server (`/home/ubuntu/cup_geosci_work_phase108`)
ran direct synchronizations against the already registered official sources.
The result was:

| Source group | Discovered | Open geoscience matches | Created | Interpretation |
| --- | ---: | ---: | ---: | --- |
| Shandong geology bureau | 2 | 0 | 0 | official pages reachable; no current publishable row |
| Henan geology bureau | 2 | 0 | 0 | notices were scanned and filtered; no current publishable row |
| Tianjin natural resources | 2 | 0 | 0 | notices were scanned and filtered; no current publishable row |
| Beijing HRSS | 12 | 0 | 0 | notices were scanned; no student-scope row |
| Fujian natural resources | 2 | 0 | 0 | notices were scanned; no student-scope row |
| CMGB Inner Mongolia | 3 | 0 | 0 | official table was scanned; no current row |
| CMGB second institute | 0 | 0 | 0 | no matching notice discovered |

These are source observations, not proof that the province has no vacancies. No
historical or unverified row was imported. The government ledger therefore did
not gain current jobs in this phase.

The CNPC browser capture still returned HTTP 412. Sinopec's one-shot browser
capture first failed while verifying `robots.txt` because the browser process
used a module-level `requests.get`, which silently honored proxy environment
variables even when `HTTP_TRANSPORT_MODE=direct` was configured. This could
misclassify a proxy certificate error as a target-site robots restriction.

## Code change

- `job_hub/browser_capture.py` now uses an explicit `requests.Session` for the
  robots check and sets `trust_env=False` in direct mode.
- `Dockerfile` explicitly installs `ca-certificates` in the slim base image.
- Regression tests cover the direct transport decision and the browser image
  contract.

Commits:

```text
b63a41a fix: install system CA certificates in browser base image
04cd964 fix: honor direct transport for browser robots checks
```

## Deployment boundary

The commits are pushed to `phase/93-official-attachment-lifecycle`. The
production web/worker and browser containers were not replaced in this phase:
the browser image rebuild stalled while downloading the 38 MB Playwright wheel
and was stopped before any `up` command. Existing containers remained healthy.
The next deployment must complete the image build (or use a prebuilt, hashed
browser image), then run one-shot CNPC/CNOOC/Sinopec captures and inspect their
status separately.

## Score effect

The honest score remains approximately **72/100**. This phase improves transport
diagnostics and prevents a false access classification, but it adds no current
student-facing jobs. The 75-point gate still requires two provinces with current
official position-table rows refreshed twice, one new job-level Three-Barrel-Oil
detail chain, and the 2026-10-10 China Earthquake Agency revalidation.
