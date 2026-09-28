# Migration Notes

No database schema migration is required. The page reads the existing versioned
`government_position_registry.json` and source-verification ledger.

Deploy with the normal application rebuild:

```bash
docker compose build web worker
docker compose up -d web worker
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T web python -m job_hub.cli government-position-audit --today 2026-09-28
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
```

Rollback is the previous phase tag/commit. No existing job row is deleted or
rewritten by this feature.
