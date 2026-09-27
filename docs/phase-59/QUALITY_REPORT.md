# Quality Report

## Local Checks

```text
python -m pytest -q tests/test_government_revalidation.py \
  tests/test_government_positions.py \
  tests/test_government_position_publish.py \
  tests/test_reports.py
27 passed

python -m compileall -q job_hub
git diff --check
passed
```

The full test suite is required before release tagging.

## Official Evidence Checks

- China Earthquake Administration official workbook:
  `646e153605e06596c21601695b1072ba1a74a8f24ec18321fd54b3d184589729`.
  The live official download matched the reviewed 35,655-byte file exactly.
- 70 China Earthquake Administration geoscience rows and 89 planned places
  remain row-level audited. The registration window is 2026-10-10 08:00 to
  2026-10-26 18:00, so it is `upcoming`, not current, on 2026-09-28.
- Direct official-evidence recheck at 2026-09-28 verified Anhui, China
  Earthquake Administration, Gansu, Hubei, Hunan and Ningxia sources. The CGS
  Development Research Center URL returned a local TLS failure and is recorded
  as `source_unavailable`; it is not interpreted as an empty official scan.
- Beijing HRSS official notice and XLSX were checked. Its deadline is
  2026-11-15, but no row explicitly matches a supported geoscience major or
  says `不限专业`, so it adds zero student-facing rows.

## Ledger State on 2026-09-28

- 136 fully evidenced records in the reviewed government ledger.
- 52 current, explicit-match records (64 planned places).
- 70 official, explicit-match records (89 planned places) scheduled to open
  on 2026-10-10.
- 14 closed rows remain audit-only and never enter student pages.

## Required Production Checks

```bash
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
docker compose exec -T web python -m job_hub.cli government-position-audit \
  --today 2026-09-28 --max-age-hours 48
docker compose exec -T web python -m job_hub.cli domain-check \
  --hostname jobs.cupdky.cn --expected-ip 81.70.62.174
```

Pass criteria: database audit has zero issues; worker heartbeat is current;
government evidence states are visible; the 70 CEA rows are absent before
2026-10-10 and appear only after a successful source recheck on or after that
date; and the domain probe returns `ready: true` only after DNS and HTTPS are
actually live.
