# Phase 62 Quality Report

## Local Verification

```text
python -m pytest -q
334 passed

Focused attachment and government-registry regression set
23 passed

Focused profile, taxonomy and government-publication set
49 passed

python -m compileall -q job_hub tests
passed

git diff --check
passed
```

The new regression coverage proves that:

- a discovered application form is stored as application material, not a
  position table;
- a real structured position row for an unrelated major does not enter the
  private review queue;
- a structured `不限专业` row with a supported degree remains reviewable;
- an unrelated government role missing its location is still rejected as a
  professional mismatch, not retained as a pending-location candidate;
- a candidate already placed in the old queue is moved to `rejected` when its
  attachment is later identified as an application form;
- a short non-year position code no longer causes the first data row to be
  merged into the workbook header.

## Server Acceptance Required

After deployment, run the reconciliation and then inspect the result before
adding new provincial sources:

```bash
docker compose exec -T worker python -m job_hub.cli reconcile-artifact-candidates
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 180
```

Acceptance requires `audit.ok: true`, a healthy worker, no public job count
increase caused by reconciliation, and a recorded count of rejected versus
retained private candidates. A rejected workflow document is evidence of a
working quality gate, not a missing recruitment opportunity.
