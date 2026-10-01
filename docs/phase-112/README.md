# Phase 112: CNOOC candidate gate and official-source refresh

## Scope

This phase fixes a false-positive failure in the CNOOC browser capture and
records a server-side official-source refresh. The change is deliberately
small: it does not relax evidence, major, degree, deadline, or publication
gates.

## Code change

`job_hub.cnooc_browser_capture._candidate_job` now accepts configurable
`exclude_patterns`. The CNOOC browser source excludes non-geoscience
functions such as audit, finance, legal, administration, procurement, risk
control, compliance, and marketing before opening detail pages. This matters
because a non-geoscience audit job mentioned “marine petroleum” in its duties
and was previously misclassified as a geoscience candidate.

The canonical capture file remains success-only. Partial, blocked, or parse
failed runs still go to the adjacent `.failure.json` file and cannot clear a
previous complete snapshot.

## Verification

- Local tests: `464 passed, 2 skipped`.
- Server CNOOC capture: `success`, 4 pages, 350 listed jobs, 187 candidates,
  187 detail successes, 0 detail failures.
- CNOOC heartbeat: `running`.
- Natural Resources Ministry API sync: completed, 0 explicit geoscience
  matches; no rows were fabricated.
- Provincial official attachment discovery completed for Anhui, Hubei, Hunan,
  Ningxia, Tianjin, Shandong, and Henan. Newly discovered attachments remain
  private until row-level evidence review; closed historical tables were not
  published.

## Acceptance decision

The CNOOC stability gate is now satisfied. The overall project is not yet at
75/100 because current provincial tables have not produced a new open source
and the 2027 China Earthquake Administration rows remain scheduled until
2026-10-10. The next acceptance work is to activate that batch only after the
opening-day recheck, add at least one new currently open province/source, and
complete one new oil-company subsidiary job-level adapter.
