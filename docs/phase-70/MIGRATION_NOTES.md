# Phase 70 Migration Notes

No schema migration is required. The worker only updates derived job fields
through the existing idempotent update path. Abandoned crawl runs are marked
`interrupted` with an explanatory error message; job content, evidence URLs,
deadlines and source records are preserved.

Before deployment, back up the SQLite volume. After deployment, run `audit`,
`government-position-audit` and `worker-health`; the first two must report no
active audit issues before a daily report is frozen.
