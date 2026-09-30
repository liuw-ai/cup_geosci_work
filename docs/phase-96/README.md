# Phase 96: Restricted Official Evidence Confirmation

## Why

The China Earthquake Administration (CEA) 2027 position workbook is an
official XLSX, but its attachment host was confirmed to disallow automated
retrieval. A daily revalidator must not turn that restriction into an HTTP
workaround or claim that the source is empty.

## Change

`cea-2027-recruitment` is now configured with
`government_evidence_recheck_mode: manual_only`.

- The daily government revalidator makes zero requests for this source.
- The worker records `manual_confirmation_required` in its operational summary
  and preserves the previous verified timestamp rather than overwriting it.
- The normal evidence freshness window still removes the rows from student
  publication when the last manual confirmation becomes too old.
- The quality report exposes the source in
  `manual_confirmation_required_sources` instead of treating it as a successful
  scan or an empty vacancy list.

## Manual operation

Before the official application window opens on 2026-10-10, an administrator
must open the CEA official notice and the official XLSX in a normal browser and
check that the notice, attachment rows, major/degree requirements, locations,
headcounts and 2026-10-10 to 2026-10-26 window are unchanged. Then run:

```text
python -m job_hub.cli confirm-government-source-evidence cea-2027-recruitment \
  --confirm \
  --note "已人工核对中国地震局官网公告、官方岗位表、专业学历、地点人数和报名时间。"
```

The command requires an existing `verified_open` row for the source and writes
only a `verified` timestamp to SQLite. It does not download the workbook or
change the reviewed ledger. The resulting rows remain subject to opening-date,
deadline, evidence-freshness and student professional-match gates.

## Acceptance

```text
python -m pytest -q tests/test_government_revalidation.py tests/test_government_position_cli.py
python -m pytest -q
python -m compileall -q job_hub tests
git diff --check
```
