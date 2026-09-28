# Phase 65: Government Reindex Publication Gate

## Purpose

Phase 64 corrected the official major taxonomy and degree parser. Applying
that correction to historical rows exposed a separate lifecycle defect:
`reindex-jobs` recalculated generic student eligibility but did not reapply the
government position ledger's application-window and freshness gates.

As a result, the China Earthquake Administration 2027 position table could be
made visible before its official application window opens on `2026-10-10`.
This phase closes that gap.

## Rule

For a reviewed government table row, a reindex may publish it only when all
of these remain true:

1. the record is present in the current reviewed official table snapshot;
2. its official application opening date has arrived;
3. its source verification and ledger freshness are current;
4. it still has explicit job-level major and degree evidence for a supported
   CUPB Geoscience School profile.

Rows outside that boundary remain in the private corpus with
`pending_evidence`; they are not silently described as unavailable or absent.

## Compatibility

Earlier controlled attachment imports may have an `artifact-candidate:`
external id. The gate recognizes an equivalent current row using the same
official source, position code, employer, deadline, official attachment and
job-level major evidence. This preserves the prior audit history without
allowing a stale or future record through.

## Regression Coverage

- A future government application window stays private after `reindex-jobs`.
- A current government attachment candidate remains public after reindexing.
- Doctoral-only roles remain visible only to doctoral profiles.
- CMGB browser-capture rejection tests use a fixed reference time so quality
  verification does not change merely because the wall clock passed a fixture
  freshness boundary.

## Production Acceptance

Before calling this phase complete in production:

1. retain the pre-reindex SQLite backup and restore it while all writers are
   stopped;
2. deploy this code and run `python -m job_hub.cli reindex-jobs` once;
3. verify that every `cea-2027-recruitment` row is private before `2026-10-10`;
4. verify that currently open Anhui, Hunan, Gansu, Ningxia, Hubei and China
   Geological Survey rows remain subject to the same official-evidence gate;
5. run `audit` and retain its output with the deployment record.
