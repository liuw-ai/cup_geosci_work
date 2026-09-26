# Phase 41 Quality Report

## Local

- Branch: `phase/41-expiry-gate`
- Tag: `v0.22.14` (implementation), follow-up quality tag `v0.22.15`
- Full test suite: `275 passed`
- Database migration: not required; existing schema is reused.

## Server

- Deployment directory: `/opt/cup_geosci_work`
- Deployment tag: `v0.22.14-server`
- Web `/healthz`: HTTP 200 from the server host.
- Worker health: `ok: true`; heartbeat is current.
- Audit: `ok: true`; `935` total records and `201` currently open student-visible jobs.
- Attachment candidate states after synchronization: `expired=148`, `needs_review=20`, `published=2`.
- The four registered official position-table artifacts were downloaded and extracted; only the Anhui artifact has a current deadline on 2026-09-26.

## Interpretation

The stage improves lifecycle correctness and queue hygiene. It does not claim that expired Shandong, Henan, or Tianjin tables are current vacancies, and it does not increase the domestic-source coverage by itself. The next expansion work must add new official, currently open source evidence rather than republishing historical rows.
