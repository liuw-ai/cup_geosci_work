# Phase 94: Historical Dynamic-Feed Intake Guard

## Verified observations

- The Natural Resources Ministry public recruitment API currently exposes four
  historical notices. Its 2026 second-batch notice explicitly closed at
  `2026-04-14 17:00`, and the parent notice endpoint now returns no position
  rows.
- The China Geological Survey mobile recruitment index retains old notices and
  process announcements across multiple years. A successful read of that
  index is not evidence of a current vacancy.
- Six high-priority provincial geology-bureau candidate entries were rechecked
  from the deployed server with direct transport. Their registered old hosts
  either failed DNS resolution, reset the connection, or could not provide a
  usable `robots.txt`. They remain unavailable, not zero-vacancy sources.

## Change

Sources may opt into `skip_expired_before_persist`. When an item is already
expired according to its official deadline, or its configured undated window,
the worker retains it only in the crawl metrics as a filtered item. It does
not create a `jobs` row or a private manual-review record. The source still
reports a successful scan, so a historical feed is never reported as an empty
or failed source.

This option is enabled only for the two observed long-lived dynamic feeds:

- `cgs-notices`: undated notices older than 45 days are expired before intake;
- `mnr-public-recruitment`: notices with an explicit elapsed deadline are
  expired before intake.

Historical fixture files and the separate government position ledger are not
changed. Their lifecycle tests continue to preserve evidence without exposing
closed vacancies to students.

## Acceptance

```text
PYTHONPATH=. pytest -q tests/test_pipeline.py tests/test_sources.py
python -m compileall -q job_hub tests
git diff --check
```

This is a queue-quality correction, not a numerical expansion. The objective
score remains 68/100 until a new current official position table or a complete
dynamic system capture passes the existing job-level gates.
