# Phase 61: Official Government Attachment Discovery

## Purpose

This phase makes official public-institution and civil-service position-table
discovery part of the daily worker. It serves CUPB School of Geosciences
students only: resource exploration engineering undergraduates, and geology,
geological engineering, or geological resources and engineering masters and
doctoral students.

It does **not** claim nationwide job coverage or automatically publish
attachments as jobs. The correct output of a daily scan can be a private
candidate, a blocked source, a failed source, or a successful scan with no
recruitment notice. These are materially different states.

## Delivered Workflow

```text
Explicit official source allowlist
  -> robots, host, title and vacancy filters
  -> official recruitment notice URL
  -> official PDF/XLS/XLSX/CSV/DOCX attachment registration
  -> controlled download, hash and parser
  -> private row-level candidate queue
  -> administrator verifies official page + row evidence
  -> existing publication gate decides student visibility
```

Only an enabled `html_notice` or `landing_page` source with
`attachment_discovery_enabled: true` can enter this workflow. The source
contract requires official hosts, official listing/direct notice URLs,
attachment hosts and a bounded notice limit. Links outside those hosts are
rejected before an attachment is registered.

The first enabled batch contains 11 already-validated official columns:

- Beijing HRSS and Beijing Natural Resources;
- Tianjin Natural Resources;
- Shandong HRSS examination and Shandong Geology Bureau;
- Henan Geology Bureau, Hubei Natural Resources and Hunan Geological Institute;
- Gansu, Anhui and Ningxia geology bureaus.

Each source is capped at eight notices per cycle and uses its declared request
interval. This is a bounded, compliant discovery pass, not a bulk crawl.

## Safety and Publication Boundary

- `blocked` means robots, access policy or a restricted notice prevented a
  scan. It never means there are no jobs.
- `failed` means network, page-structure or processing failure. It never means
  there are no jobs.
- `no_notice_sources` means the configured official listing completed but had
  no recruitment notice satisfying its existing source filters.
- A discovered attachment produces no public job by itself.
- A row needs official notice, official attachment row, explicit professional
  eligibility or unrestricted-major evidence, degree, location, headcount and
  deadline evidence before the existing student publication gate can pass.
- The daily expiry mechanism continues to remove closed public jobs and expires
  unreviewed private candidates without deleting their evidence.

## Operations

The worker runs discovery after normal official source synchronization and
before its registered attachment-processing queue, so a newly discovered
official table can reach private review during the same daily cycle.

Manual, read-only verification of the configured batch:

```bash
docker compose exec -T worker python -m job_hub.cli \
  discover-configured-government-artifacts
```

To inspect a single registered source without scanning the full batch:

```bash
docker compose exec -T worker python -m job_hub.cli \
  discover-configured-government-artifacts --source-id shandong-hrss-exam
```

The command may register attachments and change the private queue. It does not
create a student-visible job and should be run from the production worker
environment, not from an unreviewed personal browser session.

## Remaining Work

This phase closes the manual-discovery gap, not the nationwide coverage gap.
The next operational work is to inspect the private candidates created from
current official tables, add successful official notices to the reviewed
government-position registry, and continue locating current columns for the
remaining provincial public-institution and civil-service sources. A current
official annual civil-service table is still required before importing any
current civil-service job rows.
