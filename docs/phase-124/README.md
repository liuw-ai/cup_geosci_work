# Phase 124: maintenance-window readiness and isolated production refresh

## Completed

- Deployed the already-reviewed CNOOC legacy-API retirement and Sinopec
  browser-only source isolation to the server through an isolated worktree at
  `/home/ubuntu/cup_geosci_work_phase123`.
- Rebuilt the web, worker, and browser-worker images from commit `9e4ced3`.
- Took a SQLite backup before the deployment and kept the existing production
  volume and the dirty legacy worktree untouched.
- Added a readiness regression for the CNPC official `00:00-06:00` maintenance
  window. A fresh `waiting` heartbeat with an explicit maintenance detail is
  now visible as an expected access limitation and does not block unaffected
  official sources. It still keeps the browser worker `ok=false` so the
  limitation is not hidden.
- Executed a direct single-source refresh for the Sichuan geology bureau. The
  official entry was reachable, but the current page produced no row passing
  the recruitment, field-evidence, professional-match, and deadline gates:
  `discovered=0`, `open_matches=0`, `created=0`, `updated=0`.

## Verification

- Local regression suite: `477 passed, 2 skipped`.
- Server audit after deployment: `1857` records checked, `0` audit issues,
  `152` student-visible open jobs.
- Server government ledger: `7` official sources verified in the latest run;
  six sources have at least two successful immutable refresh events. The CEA
  2027 table remains scheduled for its official opening on `2026-10-10` and is
  not published early.
- CNPC maintenance is reported as an expected limitation. Sinopec remains
  explicitly degraded because the current browser container could not verify
  its TLS certificate chain; this is not treated as an empty result.

## Not claimed

This phase does not add a new current vacancy source and does not claim that
Sichuan has no jobs. The national civil-service table, additional current
provincial tables, and an independent current Three-Barrel-Oil subsidiary
detail source remain open expansion work. Domain/DNS remains intentionally
outside this phase.

## Next gate

1. Recheck the Sinopec server TLS/robots path without disabling certificate
   verification.
2. On `2026-10-10`, revalidate the China Earthquake Agency notice and XLSX;
   only then activate its 91 verified matching rows.
3. Keep searching for a current, independently evidenced subsidiary detail
   source and a second current provincial position table; each must pass two
   server refreshes before it affects the public count.
