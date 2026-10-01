# Phase 127: public-read lifecycle gates and retired-source health separation

## Completed

- Audited the public read paths before changing source coverage. The audit
  found two real failure modes: historical errors from disabled replacement
  sources were reported as current outages, and a public `open` row could
  remain readable until the worker's next expiry pass.
- Coverage now keeps historical health distribution for audit, adds an
  enabled-source distribution, excludes disabled sources from current
  `unhealthy_sources`, and exposes retired/replaced sources separately with
  their replacement id and reason.
- The sources page labels retired/replaced entries as `已停用` instead of
  `待核验`, so an old adapter is not mistaken for an unvalidated candidate.
- Public job lists, profile-filtered lists, categories, job details, daily
  report display, report counts, and `/healthz` accept the configured local
  business date and hide rows whose explicit deadline has passed. Internal
  historical queries remain available without the public date gate.

## Verification

- Local regression suite: `481 passed, 2 skipped`.
- `python -m compileall -q job_hub`: passed.
- Flask application import and route registration: passed (`39` routes).
- Added regressions for retired-source health separation, read-side deadline
  filtering, and `/about` template rendering.
- No production deployment was performed in this phase; the user's current
  server worktree and database volume were not touched.

## Honest score

The breadth/coverage score remains approximately **74/100**. This phase
improves correctness and protects the existing 146/152-row production corpus,
but it does not claim a new official source or inflate the vacancy count. The
domain/DNS item remains outside the score, as requested.

## Next gate

1. Revalidate one independent current provincial official position-table
   source twice through the server before activating any new rows.
2. Revalidate one independent Three-Barrel-Oil subsidiary detail source twice;
   a list endpoint alone is not sufficient.
3. On `2026-10-10`, recheck the China Earthquake Agency notice and XLSX after
   its announced opening time, then activate only rows that pass the same
   official evidence, deadline, major, and degree gates.
4. Keep the national civil-service table unimported until the current annual
   official table is confirmed; historical tables remain audit-only.
