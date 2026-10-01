# Phase 104: Source freshness retirement gate

## Problem found

Snapshot and browser adapters already rejected captures older than their
configured `max_age_hours`. However, a failed/blocked run left the previous
student-visible rows open in SQLite. The health report therefore said a source
was stale while the public job count still included its old rows. This was
especially unsafe for dynamic CNPC, Sinopec, PipeChina and browser captures.

## Change

- `JobPipeline.expire_stale_source_jobs()` checks only sources that explicitly
  declare `config.max_age_hours`.
- Freshness is measured from `sources.last_synced_at`, which is updated only
  after a complete successful source sync; a robots/HTTP/parse failure cannot
  refresh it.
- Once the window expires, only currently student-visible rows are marked
  `withdrawn` and an auditable `job_events` record explains the retirement.
  Historical rows and official evidence remain in the database.
- A later complete successful scan writes the same fingerprint again and
  reopens the row through the existing upsert path.
- The daily worker runs this gate after source synchronization and includes the
  withdrawal counts in its structured log.

Sources without an explicit freshness contract are unchanged. This preserves
the distinction between a live official notice source and a frozen browser or
snapshot source, and does not turn an unavailable source into a false
"no vacancies" result.

## Verification

```text
python -m pytest -q tests/test_source_freshness.py tests/test_pipeline.py
24 passed

python -m pytest -q
453 passed, 1 skipped

python -m compileall -q job_hub tests
git diff --check
```

## Deployment check

After the server image is rebuilt, run one controlled worker sync and inspect
`stale_source_withdrawals` in the worker log. The resulting public count must
be interpreted together with source health: withdrawn rows are not new
vacancies and must not be counted as expansion. The next expansion task is to
add a fresh, official job-level source, not to restore stale snapshots.
