# Phase 95: Historical Notice Queue Hygiene

## Evidence

An isolated, robots-aware direct scan on 2026-09-30 established two source-
specific facts:

- `ccgc-careers` discovered 18 official notices. Twelve would otherwise have
  been persisted as expired, private `pending_evidence` records. They included
  2025 and closed 2026 recruitment announcements; the scan had zero current
  student matches.
- `hunan-geology-institute` discovered a closed internal-selection notice with
  an official 2026-09-10 deadline. It also had zero current student matches.

These are not vacancies and are not evidence that either source is empty. The
same isolated database audit found zero student-visible jobs from these scans.

## Change

Both observed long-lived official notice feeds now opt into the existing
`skip_expired_before_persist` policy. A row already expired by an official
deadline, or by the source's verified 45-day undated window, remains counted
as a filtered scan item but is not written to `jobs` and cannot consume the
private attachment-review queue.

The policy is deliberately not enabled for unobserved sources. A source may
use a historical index as a parser fixture, so that decision must remain
explicit and evidence-based.

## Boundaries

- This phase does not create or publish a job.
- It does not treat a TLS or robots failure as an empty source.
- It does not change the job-level professional, degree, location, headcount,
  deadline, or official-evidence gate.
- The separate official position-table ledger remains the route for the 52
  current verified government rows and the China Earthquake Administration
  opening window on 2026-10-10.

## Reference Project Review

`参考工具/wehire-monitor-main` was reviewed selectively. Its useful lesson is
bounded work and explicit outcomes: do not spend the reviewer budget on a
long historical feed before it has passed a current-vacancy gate. The project
already has stronger equivalents for this service: per-source item caps,
source run ledger states, expiry lifecycle, controlled attachment queue, and
official-evidence publication gates. Its credentialed WeChat fetcher,
anti-rate-limit behavior and login-dependent collection are intentionally not
adopted because they violate this service's public-source-only boundary.

## Acceptance

```text
PYTHONPATH=. pytest -q tests/test_pipeline.py tests/test_sources.py
python -m compileall -q job_hub tests
git diff --check
```
