# Quality Report

## Local Verification

```text
pytest -q
329 passed

python -m compileall -q job_hub tests
docker compose config --quiet
load_source_registries(data/sources.json, data/provincial_sources.json)
78 registered official sources; 11 discovery-enabled sources
git diff --check
passed
```

The new regression coverage verifies that:

- only enabled sources explicitly opting in are scanned;
- announcement URL discovery preserves configured recruitment/title filters
  and rejects non-official hosts;
- robots/access blocks and collection failures are counted separately from a
  successful no-notice result;
- discovery registers a private artifact but creates no public job;
- malformed attachment-discovery source configuration is rejected at registry
  load time.

## Production Acceptance Required

After clean deployment, run:

```bash
docker compose exec -T worker python -m job_hub.cli \
  discover-configured-government-artifacts
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
```

Acceptance is not a raw attachment count. It requires each new public job to
retain an official announcement, official attachment row, professional and
degree decision, location, headcount and deadline evidence. A source failure,
blocked notice or unreviewed candidate is visible to administrators but is not
reported as an active student opportunity.
