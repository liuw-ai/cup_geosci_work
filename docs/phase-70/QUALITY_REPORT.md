# Phase 70 Quality Report

## Local verification

```text
python -m pytest -q tests/test_pipeline.py tests/test_reports.py tests/test_app.py: 32 passed
```

The new recovery regression confirms that an abandoned run is interrupted,
while a recent run remains `running` and continues to block publication.

## Production verification

On 2026-09-28 the server had 1,427 persisted jobs and 197 student-visible
current jobs. One abandoned Halliburton crawl run (id 1085) was recovered
without deleting job data. The resulting audit passed with zero issues; the
government ledger remained at 52 current verified rows, 70 upcoming rows and
zero current civil-service rows.

## Acceptance boundary

This phase improves daily reliability and rule consistency. It does not claim
that the national civil-service table has been published, and it does not
turn pending or out-of-scope records into public jobs.
