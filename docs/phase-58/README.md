# Phase 58: Daily Source-Task Lease Recovery

## Purpose

This phase repairs a production reliability gap in the daily update path. The
student site serves CUPB School of Geosciences students only when a vacancy has
official, role-level evidence for the supported degree and discipline. That
gate is useful only if the official-source queue continues to run after a
worker interruption.

## Change

`source_tasks` now has an explicit expired-lease recovery operation:

1. A task may be claimed only while it has a valid lease.
2. At the beginning of every full sync, expired `running` task leases are
   returned to `pending`.
3. The task retains its attempt history and records `lease_expired` for the
   administrator; it is not silently described as a successful scan.
4. The same full sync can claim and retry that source normally.

`crawl_runs` and `source_tasks` remain separate. An interrupted crawl record
is historical evidence; an expired queue lease is scheduling state. Recovering
one does not conceal a failure in the other.

## Scope Boundary

This phase does not publish a new vacancy or weaken the profession, degree,
location, deadline, or official-evidence gate. It does not treat CNPC HTTP 412,
PipeChina robots HTTP 403, or an empty current official listing as evidence of
no jobs.

## Server Findings Before Deployment

- CNPC official entry: HTTP 412 from the server's direct network; browser
  capture remains access-limited and is not published as a zero-result scan.
- PipeChina `robots.txt`: HTTP 403; its dynamic capture remains disabled.
- COSL official portal: entry and robots are accessible, but the 2026-09-28
  read-only scan returned zero current rows.
- MNR public recruitment API: one current item was scanned, with zero role
  rows passing the explicit geoscience publication gate.
- Three server queue tasks were found with expired leases while still marked
  `running`; this phase addresses that scheduler defect.
