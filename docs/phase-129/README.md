# Phase 129: validate row-level opening dates before runtime

## Completed

- Audited the government position-table loading, revalidation, publication,
  and reconciliation path before making changes.
- Found and fixed a real contract gap: an optional row-level `opening_date`
  was accepted without date validation, so a malformed hand-edited ledger
  could load successfully and fail later during a worker/public-read cycle.
- Normalized valid row-level opening dates during registry validation.
- Added support for an optional batch-level `opening_date`, so a batch can
  carry its official application opening boundary without duplicating it in
  every row.
- Added regressions for malformed row dates and batch opening-date activation.
- Found and fixed a second lifecycle regression during the same audit:
  `not_configured` verification results now fail closed instead of falling
  back to the static registry snapshot.  `source_unavailable` keeps its
  existing short-lived last-success behavior, while an unconfigured source
  has no publication authority.

## Verification

- Government lifecycle tests: `37 passed`.
- Full local suite: `484 passed, 2 skipped`.
- `python -m compileall -q job_hub`: passed.
- Flask application import and route registration: passed (`39` routes).
- No new vacancy was imported, no production database was changed, and no
  server deployment was performed in this phase.

## Honest score

The score remains approximately **74/100** (excluding domain/DNS and
production deployment). This phase improves failure containment and does not
count as source expansion.

## Next gate

1. Revalidate the China Earthquake Agency official XLSX on/after `2026-10-10`
   and activate only rows that pass the official evidence, major, degree, and
   deadline gates.
2. Finish one independent provincial official position-table adapter and prove
   two successful server refreshes before publishing its rows.
3. Finish one Three-Barrel-Oil subsidiary detail adapter and prove two complete
   detail-level refreshes; a failed/partial scan must not clear existing rows.
4. Keep annual civil-service rows audit-only until the current official table
   is confirmed; never import historical positions as current vacancies.
