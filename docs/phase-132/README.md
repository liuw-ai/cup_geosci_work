# Phase 132: CNOOC detail snapshot and disabled-source publication gate

## Why this phase exists

The previous audit found two separate issues that affected the student-facing
result:

1. A complete CNOOC 2027 browser capture was available, but it had not been
   placed at the configured runtime path, so the source could not contribute
   current positions through the normal pipeline.
2. Disabling an automatic source stopped future collection but did not protect
   old `open` rows at the read layer.  Retired Sinopec rows could therefore
   remain visible until a background withdrawal completed.

This phase addresses both without relaxing the major/degree evidence gate.

## Verified CNOOC capture

The runtime capture was validated by `load_cnooc_browser_capture` with the
configured 30-hour freshness window:

- captured at `2026-10-01T02:40:42Z`;
- 350 list records across 4 pages;
- 343 candidate rows and 343 detail pages discovered;
- 343 details succeeded, 0 failed;
- pagination complete and 343 rows exported;
- 96 rows contain geoscience signals before the normal student gate.

The capture is stored under `APP_DATA_DIR/verified/cnooc-browser-2027.json`.
It is a runtime artifact, not a static repository fixture. The CNOOC browser
worker must produce a fresh file before the 30-hour window expires; a stale or
partial capture remains private and enters the source failure path.

The normal sync imported 343 official detail rows:

- 48 rows passed the student-facing major/degree gate;
- 251 rows remain open but `pending_evidence`/manual-review only;
- 44 rows remain out of student scope;
- every published CNOOC row uses an official Zhaopin detail URL and retains
  the captured field evidence.

## Publication gate fix

Student-facing reads now join the source registry and require either:

- an enabled source; or
- a `manual` source, whose rows are explicitly verified administrator imports.

This rule is applied to list, detail, category, deadline, report and count
queries. Internal/audit queries still retain disabled-source history. The
existing `official-manual-import` workflow therefore remains compatible while
retired automatic snapshots cannot leak through a read-side race.

The 72 open Sinopec rows from the disabled legacy source were withdrawn with
an audit reason; they were not deleted. The fresh browser successor remains a
separate source and can be promoted after its own capture succeeds.

## Verification

- `490 passed, 2 skipped` before this phase's new regression;
- the focused regression suite passes, including disabled-source and manual
  source behavior;
- `python -m compileall -q job_hub` passes;
- `git diff --check` passes;
- the refreshed daily report passes the database audit with zero issues;
- current report: 48 new, 1 expired, 128 open student-facing rows.

## Remaining gates

This phase does not claim the full national target. The next production gates
are:

1. run the CNOOC worker on the server again before the capture ages out and
   retain two consecutive successful runs;
2. generate a fresh complete Sinopec 132-unit/35-candidate capture and switch
   the successor source only after its detail evidence passes;
3. complete two server-side official refreshes for at least two independent
   provincial public-institution sources;
4. add the current civil-service position table only after the official annual
   attachment is confirmed;
5. keep CNPC, PipeChina and other browser captures on the same complete-scan
   and stale-withdrawal policy.
