# Phase 97: Enforce Government Attachment Lifecycle Policies

## Why this phase exists

The government artifact manifest previously recorded statuses such as
`manual_verified` and `historical_closed`, but registration treated every row
as `registered`. A later batch run, or a direct administrator
`process-artifact` command, could therefore download a file that policy had
marked as manual-only or unsuitable for the student audience.

## Enforced policy

Only `server_download_pending` artifacts are registered as `registered` and
may be downloaded by the controlled attachment processor. The following are
registered as `skipped` and are blocked even for a direct processing command:

- `manual_verified`: evidence was checked manually; no automated re-fetch.
- `historical_closed`: retained only for parsing regression and evidence.
- `source_unavailable`: no automatic retry or "no jobs" conclusion.
- `current_non_student_eligible`: real current recruitment that does not suit
  current CUPB Geoscience students.

This affects only the private attachment ledger. It does not publish jobs and
does not claim an unavailable or excluded source has no recruitment.

## China Coal Geology Group result

The official China Coal Geology Group notice and XLS were confirmed from the
server on 2026-09-30. The attachment is titled
`2026年中煤地质集团有限公司人员调配计划明细表`, contains 8 rows and 13 planned
headcount, and has SHA-256:

```text
4d23a2558d490f32f1c31b1ac9c7f365949a48d2fe7a60964109acc80b1aa40f
```

It is a mature-talent/personnel-transfer plan. Relevant-looking rows require
existing work or project experience, professional titles, qualifications, or
construction licences. It is therefore preserved as a real official source
but classified `current_non_student_eligible`, not discarded and not shown as
a student opportunity.

## Verification

```powershell
python -m pytest -q
python -m compileall -q job_hub tests
git diff --check
```
