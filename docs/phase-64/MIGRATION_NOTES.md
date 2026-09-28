# Phase 64 Migration Notes

No schema migration is required. Existing job records are reconciled during the
next worker cycle. The database retains the original attachment evidence and
event history; the fix changes only the reconciliation identifier set and
derived publication decision.
