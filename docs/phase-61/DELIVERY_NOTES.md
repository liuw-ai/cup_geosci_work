# Delivery Notes

- Branch: `phase/61-provincial-position-table-expansion`
- Review tag: `phase-61-review`
- Database migration: none
- Public-job schema and student publication gate: unchanged
- Private-data behavior: official attachments may be newly registered and
  parsed on the server; their candidates remain `needs_review` until an
  administrator verifies them.

Rollback is a standard Git rollback to `phase-60-review`, followed by a clean
Compose deployment of that release. The SQLite volume is intentionally not
replaced or deleted: Phase 61 only adds private evidence and candidates, and
the prior release safely ignores the extra private ledger records.

No third-party source, public account, Zhonggong, Huatu, historical table or
search result is added as student-facing evidence by this delivery.
