# Migration Notes

No SQLite schema migration is required. Existing `source_tasks` rows already
contain `status`, `lease_until`, `last_error`, and `last_error_class`.

After deployment, the next worker `sync_all` pass automatically recovers only
rows satisfying all of these conditions:

- `status = running`
- `lease_until` is present
- `lease_until` is earlier than the current UTC time

Recovered rows are set to `pending` and become eligible immediately. Rows with
an active lease, completed rows, and policy-blocked rows are untouched.

Use the protected administrator task endpoint or this command to confirm the
result:

```bash
docker compose exec -T web python -m job_hub.cli source-tasks
```

The expected audit trail for an automatically recovered task is
`last_error_class = lease_expired`; a subsequent successful collection changes
the task to `succeeded`.
