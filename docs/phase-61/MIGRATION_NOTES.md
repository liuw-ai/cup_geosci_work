# Migration Notes

There is no SQLite schema migration in Phase 61.

The only durable runtime changes are:

1. Selected source configurations gain `attachment_discovery_enabled` and a
   bounded `attachment_discovery_max_notices` value.
2. The worker invokes official-notice attachment discovery before processing
   registered files.
3. Newly discovered official files are stored through the existing
   `source_artifacts`, `source_artifact_rows` and `artifact_job_candidates`
   private-ledger tables.

Existing public jobs are not reclassified or republished by this phase. The
existing deadline cleanup remains authoritative for both public jobs and
unreviewed attachment candidates.
