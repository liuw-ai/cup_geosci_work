# Phase 68: Production Gate Consistency And Coverage Recheck

## Objective

Keep the student-facing list limited to currently open, officially evidenced
geoscience opportunities while correcting a production mismatch between the
government ledger, reindex code, and the CMGB source handover.

## Delivered

- Deployed the `source_opening_dates` gate and the latest government ledger.
  China Earthquake Administration 2027 rows remain private until the official
  registration window opens on `2026-10-10`.
- Fixed reindexing so taxonomy maintenance cannot reopen `expired`,
  `withdrawn`, or `superseded` historical rows.
- Repaired the interrupted CMGB snapshot-to-browser transition. The old
  snapshot remains auditable history and is no longer public or duplicated.
- Corrected the production source label to state that the dynamic capture is
  enabled.
- Reindexing now preserves the government position type category, so current
  public-institution rows appear under `事业单位与人才引进` instead of being
  silently mixed into a geology source category.
- Rechecked ten official sources without promoting any unverified or expired
  vacancy.

## Boundary

The formal domain is still waiting for an external DNS A record. This phase
does not claim that a server can create DNS records without access to the
domain provider account.
