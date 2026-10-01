# Phase 120: business-date boundary and official-source recheck

## What changed

This phase fixes a production date-boundary bug. Government position audits run
without `--today` now use `Settings.timezone` (production: `Asia/Shanghai`)
instead of Python's host `date.today()`. The explicit `--today` override remains
available for historical audits. The same rule is applied to the cross-ledger
audit. This prevents a UTC-hosted container from showing the previous day's
freshness, opening-date, or expiry decision during the first hours of a China
local day.

The lightweight `Dockerfile.business-date` overlay makes this change
deployable on a server that already has the phase119 dependency image. It copies
only `job_hub/cli.py`; it does not replace the database volume or browser
workers.

## Server verification (2026-10-02 Asia/Shanghai)

- Web and worker: healthy after overlay recreation.
- `government-position-audit` default date: `2026-10-02`.
- `cross-ledger-audit` default date: `2026-10-02`, `ok=true`.
- Database audit: `1856` records checked, `0` audit issues, `152` open student
  jobs.
- Backup: `/var/lib/job-hub/backups/job_hub-20261001T172514Z.sqlite3`, SQLite
  integrity and required-table checks passed.
- Browser status: CNOOC capture healthy; CNPC HTTP 400 and Sinopec robots 403
  remain explicitly access-limited and are not treated as no-job results.

## Source recheck outcome

The server rechecked COSL, the Ministry of Natural Resources platform, China
Geological Survey, China Coal Geology, and configured Shandong/Henan/Tianjin/
Anhui/Hubei/Ningxia official attachment sources. No new current student-eligible
row passed all evidence and experience gates in this run. The unchanged public
count is intentional; expired or mature-talent rows remain private or withdrawn.

## Gate status

The honest score remains approximately **73/100**. This phase improves daily
correctness and deployability but does not satisfy the 75-point expansion gate.
The next score-bearing work is a current provincial official position table
refreshed twice, followed by the 2026-10-10 China Earthquake Agency opening
revalidation and an independent current Three-Barrel-Oil subsidiary detail
source.
