# Phase 68 Migration Notes

## Production commands executed

```bash
docker compose build web worker
docker compose up -d web worker
docker compose exec -T web python -m job_hub.cli cmgb-production-transition --confirm
docker compose exec -T web python -m job_hub.cli reindex-jobs
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T web python -m job_hub.cli government-position-audit --today 2026-09-28
docker compose exec -T web python -m job_hub.cli coverage --record
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 180
```

## Data safety

Before deployment, the server database and the previous three Python modules
were copied to `/opt/cup_geosci_backups/phase68/`. No public job rows were
deleted. The 33 retired CMGB snapshot rows are retained with status
`superseded` and their original official evidence.

## Rollback

Stop `web` and `worker`, restore the pre-deployment SQLite backup, restore the
three backed-up modules, and rebuild the image. The formal source registry and
the runtime source switch are independent, so re-enabling the reviewed snapshot
is sufficient to roll back the CMGB handover.
