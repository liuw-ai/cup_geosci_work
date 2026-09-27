# Migration Notes

## SQLite

`government_source_verifications` is created by the existing idempotent
`Database.initialize()` schema migration. It records one controlled evidence
check per government source:

- `status`
- `checked_at`
- `last_success_at`
- `detail`
- `evidence_fingerprint`

No existing job, source, attachment, or daily-report row is deleted by the
migration. Keep the existing SQLite Docker volume during deployment.

## Versioned Ledger

`data/government_position_registry.json` gains:

```json
"source_opening_dates": {
  "cea-2027-recruitment": "2026-10-10"
}
```

This date comes from the China Earthquake Administration's official 2027
notice. The worker retains its evidence before 2026-10-10 but does not publish
the rows to the student-facing open-job list until that date.

## Deployment Order

1. Deploy the versioned release while preserving `.env` and Docker volumes.
2. Run `docker compose up -d --build web worker`.
3. Let the worker execute one sync, then run the checks in `QUALITY_REPORT.md`.
4. Do not manually advance `as_of` merely to keep jobs visible. The worker's
   successful official evidence recheck is now the production freshness clock.
