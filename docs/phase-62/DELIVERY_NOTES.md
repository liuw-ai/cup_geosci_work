# Phase 62 Delivery Notes

- Branch: `phase/62-attachment-candidate-gate`
- Review tag: `phase-62-review`
- Database migration: none
- Public-job schema and public student gate: unchanged
- Private-ledger behavior: newly parsed files and old extracted files are
  filtered by attachment purpose and row-level CUPB Geoscience eligibility.

Rollback is a standard Git rollback to `phase-61-review`, followed by a clean
Compose deployment of that release. The SQLite volume is intentionally not
replaced or deleted. Phase 62 only adds attachment metadata and may mark
unreviewed private candidates as `rejected`; the raw official evidence remains
available for an administrator to audit or restore manually.
