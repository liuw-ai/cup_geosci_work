# Phase 100: Government Freshness and Read-Only Review Gates

## Government freshness parity

The government-position ledger is a reviewed snapshot, not a permanent list
of open jobs. The daily worker already removes a row when its official source
evidence is older than `GOVERNMENT_POSITION_MAX_AGE_HOURS` (48 hours by
default). The administrator CLI previously omitted that limit unless an
operator supplied it manually. As a result, an old ledger could be reported
as current by the audit command while the student-facing worker had correctly
withdrawn the same rows.

`government-position-audit` now uses
`GOVERNMENT_POSITION_MAX_AGE_HOURS` by default. `--max-age-hours` remains an
explicit diagnostic override. The audit and daily worker therefore use the
same default freshness rule.

This phase also records the 2026-09-30 direct probe of the Sichuan Provincial
Geology Bureau. The official notice index is reachable, but the current page
had no original recruitment notice that could supply student-facing job
evidence. It is registered as an `attachment_discovery_feed`: future matching
notices enter the private attachment queue first, while the source remains a
candidate in the provincial matrix. It cannot emit a job row by itself.

## Read-only provincial review gate

## Problem

The provincial entry probe and the cross-ledger audit were presented as
read-only operations, but the CLI initialized the normal service stack before
running them. `Database.initialize()` executes schema migration and source
bootstrap writes. Running several province probes together could therefore
compete for the shared SQLite writer lock and fail with `database is locked`.

## Change

- `provincial-entry-probe` now loads settings and performs only transient HTTP
  checks. It does not initialize SQLite or bootstrap sources.
- `cross-ledger-audit` now runs before the service stack and reads only the
  versioned ledgers.
- `provincial-matrix-audit` now uses `Database(read_only=True)`. The connection
  opens SQLite in URI `mode=ro`, skips journal-mode changes, and cannot create
  or migrate tables.
- A regression test asserts that the read-only database cannot execute DDL and
  that the probe CLI never calls `services()`.

## 2026-09-30 probe result

The candidate matrix contained 38 entries. In the local environment one was
explicitly blocked by robots and 37 were unavailable because of HTTP 502/404 or
transport/TLS failures. These outcomes are recorded as access or source
availability facts; none are converted into a no-vacancy conclusion or an
enabled collector. A server-side direct probe remains the required next step.

## Operator verification

```text
python -m pytest -q
python -m pytest -q tests/test_government_position_cli.py tests/test_government_positions.py tests/test_government_revalidation.py
python -m job_hub.cli government-position-audit --today 2026-09-30
python -m job_hub.cli cross-ledger-audit --today 2026-09-30
python -m job_hub.cli provincial-entry-probe \
  --province 山东 --province 河南 --province 天津 \
  --output /var/lib/job-hub/reports/provincial-entry-probe-$(date +%F).json
python -m job_hub.cli provincial-matrix-audit \
  --output /var/lib/job-hub/reports/provincial-matrix-audit-$(date +%F).json
```

The report is diagnostic only. A source enters the student-facing pipeline
only after an official current notice, a job-level position table/detail, the
major and degree evidence, location/headcount/deadline fields, a parser
fixture, and a recurring refresh rule have all passed review.
