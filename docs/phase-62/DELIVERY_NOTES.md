# Phase 62 Delivery Notes

- Branch: `phase/62-attachment-candidate-gate`
- Initial review tag: `phase-62-review`
- Corrected review tag: `phase-62.1-review`
- Database migration: none
- Public-job schema and public student gate: unchanged
- Private-ledger behavior: newly parsed files and old extracted files are
  filtered by attachment purpose and row-level CUPB Geoscience eligibility.

The corrected release changes the order of the existing public publication
decision: a government row must first prove target-major or unrestricted-major
eligibility before a missing location can be marked as pending. This prevents
unrelated medical or biological rows from entering the private queue under the
pending-location label.

Rollback is a standard Git rollback to `phase-61-review`, followed by a clean
Compose deployment of that release. The SQLite volume is intentionally not
replaced or deleted. Phase 62 only adds attachment metadata and may mark
unreviewed private candidates as `rejected`; the raw official evidence remains
available for an administrator to audit or restore manually.
