# Phase 67: Dynamic Browser Resource Lifecycle

## Problem

The server's CMGB/国聘 browser worker successfully collected all official
detail pages, but its shared CDP browser accumulated renderer processes over
multiple runs. That left too little memory for other official SPA sources and
caused `ERR_INSUFFICIENT_RESOURCES`. This is an operational defect, not a
reason to report a source as having no current vacancies.

## Change

The server's `chromedp/headless-shell` exposes one dedicated default CDP
context and rejects incognito-context creation. Each CMGB capture therefore
closes all pages in that dedicated context before it starts and again in a
`finally` block. All list, popup, and detail tabs created by the capture are
released on success, timeout, or parsing error. The shared browser process
remains available to its worker and is not closed by a single capture.

## Verification And Rollback

- Targeted regression suite: `36 passed`.
- The server rollout must restart only the CMGB headless/browser pair, execute
  one complete public capture, and compare Chromium process count and memory
  before/after the following scheduled interval.
- A failed or partial capture continues to preserve the previous complete
  `captures/cmgb.json`; its diagnostic is written separately as
  `captures/cmgb.failure.json`.
- The Git commit and phase tag created for this change are the rollback point.

## Boundary

This change improves the reliability of a verified official dynamic source. It
does not increase the published count by inference and does not make a
non-geoscience or expired row visible to students.
