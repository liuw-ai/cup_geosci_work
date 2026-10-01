# Phase 113: Daily source ledger and current-source gate

## Server audit

On 2026-10-01 the production ledger contained 225 source-run records:

- 29 `success_with_matches` runs;
- 107 `success_without_matches` runs;
- 72 `manual_review_required` runs;
- 13 `parse_failed` runs;
- 3 transport-unavailable runs and 1 access-limited run.

The run ledger therefore distinguishes a completed empty scan from a blocked
or failed source. A source failure is not published as “no suitable jobs”.

The current student-facing snapshot remains 146 jobs. CNOOC is current and
healthy at 46 published explicit matches. The configured provincial official
entries were scanned again; no new currently open, row-level verified
geoscience position table was found in this run.

## Important freshness decision

The local Sinopec browser snapshot contains 132 units, 35 candidate units and
363 job rows, but its capture timestamp is older than the 30-hour publication
window and the server cannot currently refresh the official SPA because the
public robots endpoint returns HTTP 403. It remains a private/stale artifact;
it is not copied into the public database.

Likewise, the 2026-09-24 PipeChina snapshot is past its 30-hour freshness gate
and the current dynamic entry remains access-limited. Its 24 historical rows
are retained as evidence only.

## Acceptance status

This phase improves daily observability and prevents stale Sinopec/PipeChina
data from being shown as current. It does not claim 75/100 because it adds no
new current vacancy rows. The next acceptance gates are:

1. On 2026-10-10, revalidate the China Earthquake Administration notice and
   attachment, then activate only rows whose opening window has begun.
2. Add one new province with a currently open official position table and two
   successful refreshes.
3. Complete one additional Three-Barrel-Oil subsidiary detail chain with a
   current capture and row-level evidence.
4. Re-run coverage, government-position-audit, source-run-ledger and
   production-readiness before revising the score.
