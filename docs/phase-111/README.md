# Phase 111: CNOOC success-snapshot protection

## Problem found

The CNOOC browser worker wrote every run to the canonical
`verified/cnooc-browser-2027.json` path.  A 342/343 detail pass was therefore
stored as `partial`; the normal source sync correctly refused that manifest,
and the freshness task withdrew the previous public rows.  This made the
student-facing count fall from 146 to 100 even though the official source had
not withdrawn those vacancies.

## Fix

`job_hub.cnooc_browser_capture.persist_cnooc_browser_capture` now treats the
configured capture path as a success-only pointer:

- complete pagination with every discovered detail succeeds atomically replaces
  the canonical manifest;
- `partial`, `access_limited`, and `parse_failed` runs are written to the
  adjacent `.failure.json` diagnostic path;
- an incomplete run can never replace the last complete evidence snapshot.

The behavior is covered by a regression test that writes a success, writes a
partial run, and verifies that the success file remains unchanged.

## Server verification (2026-10-01)

- browser capture: 4 pages, 350 listed, 343 geoscience candidates;
- detail capture: `343/343`, failures `0`, pagination complete;
- source sync: 343 discovered, 46 explicit student-eligible matches;
- student-visible open jobs: 146 total, including 46 from CNOOC;
- non-geoscience or incomplete-field rows remain outside the student gate.

The previous withdrawal events remain in SQLite for audit.  They were not
deleted or rewritten; the successful capture and a normal source sync reopened
the matching rows with the existing evidence contract.

## Next acceptance work

This phase fixes lifecycle correctness but does not by itself satisfy the
75-point expansion gate.  The next work is to revalidate two current
provincial official tables twice, activate the China Earthquake Agency batch
only after its announced opening date, and add one more job-level official
sub-unit adapter without weakening the student matching gate.
