# Delivery Notes

- Branch: `phase/58-dynamic-energy-and-current-government-scan`
- Intended tag: `phase-58-review`
- Rollback: return code to `phase-57-review` and redeploy. The database schema
  does not change, so the existing SQLite volume remains compatible.

This phase improves daily-operation recovery only. It does not claim that the
national civil-service job table, all 31 provincial source roles, or fully
automated CNPC/PipeChina details have been completed. Those remain separate,
evidence-gated expansion work.
