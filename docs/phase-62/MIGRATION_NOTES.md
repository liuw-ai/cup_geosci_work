# Phase 62 Migration Notes

There is no SQLite schema migration.

The phase reuses the existing `source_artifacts`, `source_artifact_rows` and
`artifact_job_candidates` ledger. Reconciliation only changes an unreviewed
candidate's existing `review_status` from `needs_review` to `rejected`, with a
bounded Phase 62 reason in `review_note`. The source attachment, raw row,
hash, storage path and official parent page remain intact.

Existing public jobs, verified candidates and published candidates are not
reclassified, removed or republished.
