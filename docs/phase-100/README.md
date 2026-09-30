# Phase 100: Government Freshness Audit Parity

## Purpose

The government-position ledger is a reviewed snapshot, not a permanent list
of open jobs. The daily worker already removes a row when its official source
evidence is older than `GOVERNMENT_POSITION_MAX_AGE_HOURS` (48 hours by
default). The administrator CLI previously omitted that limit unless an
operator supplied it manually. As a result, an old ledger could be reported
as current by the audit command while the student-facing worker had correctly
withdrawn the same rows.

## Change

`government-position-audit` now uses
`GOVERNMENT_POSITION_MAX_AGE_HOURS` by default. `--max-age-hours` remains an
explicit diagnostic override. The audit and the daily worker therefore use the
same default freshness rule.

This phase also records the 2026-09-30 direct probe of the Sichuan Provincial
Geology Bureau. The official notice index is reachable, but the current page
had no original recruitment notice that could supply student-facing job
evidence. It is registered as an `attachment_discovery_feed`: future matching
notices enter the private attachment queue first, while the source remains a
candidate in the provincial matrix. It cannot emit a job row by itself.

## Verification

```powershell
python -m pytest -q tests/test_government_position_cli.py tests/test_government_positions.py tests/test_government_revalidation.py
python -m job_hub.cli government-position-audit --today 2026-09-30
```

On the current static ledger, the latter must report `registry_freshness` as
`stale` and must not count the old snapshot as a current student-facing
position. A new official revalidation, rather than editing `as_of`, is needed
to make rows current again.
