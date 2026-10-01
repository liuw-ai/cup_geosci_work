# Phase 101: CNOOC 2027 detail-evidence pipeline

## Scope

This phase corrects the CNOOC 2027 campaign path. The old 2026 organization
number (`104879`) returned a valid-looking `totalNum=0`, which was incorrectly
treated as the current campaign. The 2027 public page exposes organization
number `105147` and its read-only list API reports 350 jobs across four pages.

The server browser scan selected 343 rows from the 350-row campaign for
detail-level inspection:

- 43 rows are clear candidates after the student-major/degree gate;
- 256 rows remain reviewable but are not student-visible without a direct
  professional match;
- 44 rows do not match the supported Earth-science profiles.

These numbers are discovery metrics, not student-visible job counts.

## Detail contract

The list API does not reliably provide `dateEnd`. The official detail page at
`xiaoyuan.zhaopin.com/job/...` contains the authoritative
`window.__INITIAL_DATA__.main.positionDetail` object. `job_hub/zhaopin_detail.py`
now parses and validates its title, employer, job number, job description,
degree, location, headcount, publication time, and closing date. An ID mismatch,
missing date, missing location, missing degree, or non-official URL fails closed.

`cnooc-career-browser` is a separate `cnooc_browser_rows` source. Its manifest
is accepted only when API pagination is complete and every candidate detail has
been opened and parsed. Partial or challenge pages remain a review/access
failure and never become an empty result.

## Current gate

The first two server browser runs completed successfully: each saw 350 list
rows across four pages, opened 343 candidate detail pages, and parsed 343/343
detail pages. Each run admitted 43 rows through the student publication gate,
kept 256 rows as pending evidence, and rejected 44 rows as out of scope. The
second run was synchronized through the ordinary source pipeline and created
no duplicates. A separate CDP timeout during a restart was retained as a
failure diagnostic and was not counted as a successful scan. The source queue
record remains `official_job_sample_verified`; the browser worker continues on
its 180-minute cycle and the 30-hour capture freshness gate remains active.

## Verification

```text
pytest -q tests/test_zhaopin_detail.py tests/test_cnooc_browser_capture.py \
  tests/test_zhaopin.py tests/test_contracts.py tests/test_source_validation.py
35 targeted tests passed; the full repository regression now passes 447 tests
with 1 skipped. The regression includes the verified CNOOC sample URL and job
IDs in the domestic source-expansion ledger; a verified source without those
fields is rejected instead of being treated as a complete source.
```

The browser capture remains separate from the ordinary HTTP worker because the
detail host may return an anti-automation challenge to plain requests. No login,
submission, or write endpoint is used.
