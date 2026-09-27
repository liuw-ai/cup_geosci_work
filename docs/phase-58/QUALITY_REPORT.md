# Quality Report

## Local Verification

```text
python -m pytest -q tests/test_source_queue.py tests/test_pipeline.py
17 passed

python -m compileall -q job_hub
git diff --check
passed
```

The tests cover both the database-level lease recovery and the full-pipeline
case where an expired task is reclaimed and collected in the same sync.

## Required Production Verification

After deployment:

```bash
docker compose exec -T web python -m job_hub.cli source-tasks
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T web python -m job_hub.cli government-position-audit \
  --max-age-hours 48
```

Pass criteria:

- no expired task remains in `running`;
- worker health is current;
- data audit has no issues;
- a stale government registry still publishes zero government vacancies rather
  than retaining old entries.
