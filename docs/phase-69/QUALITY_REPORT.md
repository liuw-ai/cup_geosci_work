# Phase 69 Quality Report

## Local verification

```text
python -m pytest -q: 350 passed
python -m compileall -q job_hub: passed
```

The new contract test returns 70 upcoming rows for the current registry date
(`2026-09-28`), all from the official China Earthquake Administration table,
with an opening date of `2026-10-10`. No current-open count is changed.

## Production baseline after the worker re-synchronization

- Student-visible current jobs: 197
- Government ledger: 52 current, 70 upcoming, 0 current civil-service rows
- Registered sources: 78; enabled sources: 40
- Database audit: passed (`1427` checked jobs, no issues after the abandoned
  Halliburton crawl run was recovered)
- Worker heartbeat: passed

The earlier pre-worker estimate of 330 was not a valid production baseline: the
worker re-applied the publication gate and correctly withdrew rows without
current official evidence. The 70 upcoming rows are not counted in the 197
current jobs. The civil-service
ledger remains empty until an official current-year position table is
published and independently verified.

## Known external blockers

- `jobs.cupdky.cn` remains `NXDOMAIN`; DNS ownership must add `jobs A
  81.70.62.174` before HTTPS can be validated.
- GitHub remote push remains dependent on repository write permission for the
  authenticated account.
