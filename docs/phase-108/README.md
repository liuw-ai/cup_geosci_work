# Phase 108: Admin activation-task endpoint

## Change

Phase 107 introduced source-level activation tasks for official position
tables. Phase 108 exposes those tasks through the protected
`GET /api/admin/government-position-tasks` endpoint so the administrator can
inspect the daily lifecycle without reading container logs or the SQLite
database directly.

The endpoint reports each batch's state (`scheduled`, `due_revalidation`,
`verified`, or `expired`), opening and deadline dates, matching row count,
headcount, and most recent evidence verification. It uses the same freshness
and manual-confirmation gates as the worker and public upcoming-position page.
It is not a publication endpoint and is never available without
`X-Admin-Token`.

## Verification

```text
python -m pytest -q tests/test_app.py tests/test_government_positions.py
31 passed
python -m pytest -q
459 passed, 1 skipped
```

Production verification must show:

```json
{
  "summary": {"total": 1, "scheduled": 1, "due_revalidation": 0},
  "items": [{"source_id": "cea-2027-recruitment", "status": "scheduled"}]
}
```

## Scope and score

This phase improves operational completeness and auditability. It does not
claim additional current vacancies. The honest project score remains about
`72/100` until new current official sources produce publishable, repeatedly
refreshed rows.
