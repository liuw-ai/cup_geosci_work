# Phase 99: Government Ledger Consistency Gate

## Purpose

Government-source expansion facts are recorded in separate ledgers for
controlled attachments, verified position rows, source-expansion work and
registered collectors. A stale or contradictory label previously required a
manual review to detect. This phase adds a read-only contract check before an
operator expands or reclassifies a source.

## Command

```powershell
python -m job_hub.cli cross-ledger-audit --today 2026-09-30
```

The command exits non-zero for an internal contradiction, while preserving a
JSON report. It does not fetch a site, write the database, change a source
status or publish a job.

## Enforced relationships

- A queue row cannot claim `access_limited` or `scan_success_no_match` if the
  same source already has verified government position rows in the registry.
- `government-position:` sample IDs in the queue must resolve to real
  position rows.
- A `current_non_student_eligible` queue decision must point to its exact
  manifest artifact through `related_artifact_id`; source, lifecycle status
  and the bidirectional link must agree.
- Every referenced `source_id` must exist in the configured source registries.

## Interpretation

The report deliberately distinguishes `verified_positions_stale` from
`no_verified_position_records`. Stale evidence proves prior official job-level
verification but cannot contribute to the current student-facing count until
rechecked. No verified rows means no conclusion about whether a source has
vacancies.

## Verification

```powershell
python -m pytest -q tests/test_government_ledger_audit.py tests/test_domestic_expansion.py tests/test_government_artifacts.py tests/test_government_positions.py
python -m job_hub.cli cross-ledger-audit --today 2026-09-30
python -m compileall -q job_hub tests
git diff --check
```
