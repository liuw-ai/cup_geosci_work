# Phase 62: Attachment Candidate Gate

## Purpose

Phase 61 proved that the daily official-announcement -> attachment discovery
chain works. Its first production scan also exposed a quality problem: one
official recruitment notice may include a position table together with an
application form, examination result, medical-check notice or hiring list.
Those files are official, but they are not open positions for students.

Phase 62 narrows the private queue for the CUPB School of Geosciences service:

- undergraduate Resource Exploration Engineering;
- masters and doctoral Geology, Geological Engineering, and Geological
  Resources and Engineering.

It does not create, publish, or invent a job. Student-visible publication
continues to require the existing official-page, row-evidence, professional,
degree, location, headcount and deadline gates.

## Delivered Gate

```text
Official recruitment notice
  -> official attachment ledger
  -> attachment-purpose classifier
       -> application / result / medical / proposed-hire material: retain evidence, reject queue
       -> possible position table: parse rows
  -> row must contain job title + major or unrestricted-major + degree
  -> existing CUPB Geoscience eligibility decision
  -> private review queue
  -> administrator verifies official page and row evidence
  -> existing public publication gate
```

The purpose classifier recognizes application forms, qualification review,
written/interview results, medical checks, investigations, proposed hires,
hiring lists, public notices, withdrawal/replacement records and operational
guides. It stores the original URL, file hash, extracted rows and a bounded
rejection reason. It never deletes an official source file.

A position row must now have all three values from the same extracted row:

1. a job/position title;
2. a professional requirement that explicitly covers a supported geoscience
   profile, or an explicit `不限专业` equivalent;
3. a degree requirement that covers at least one supported profile.

Rows outside this scope do not enter the private queue. A genuinely matching
government row missing only a row-level location remains reviewable, because
the administrator can attach the official notice's location evidence before
publication. All other missing-evidence cases remain out of queue.

## Corrective Reconciliation

Already extracted files can be rechecked without fetching the network or
touching public jobs:

```bash
docker compose exec -T worker python -m job_hub.cli \
  reconcile-artifact-candidates
```

To inspect one attachment first:

```bash
docker compose exec -T worker python -m job_hub.cli \
  reconcile-artifact-candidates --artifact-id 123
```

Only `needs_review` candidates are automatically transitioned to `rejected`.
`official_content_verified` and `published` candidates are deliberately left
untouched. This prevents a parser rule change from overriding a prior human
decision. The command reports processed attachments, retained candidates and
rejected candidates; it never adds a student-visible job.

## Parser Robustness Fix

The workbook header detector now stops after a rich first header row. Before
this correction, a first data row containing values such as an employer name
and `专业` could be merged into the header when a portal used a short code such
as `A-001`. That produced zero extracted rows from a valid table. Multi-row
headers still work when the first header row is incomplete.

## Remaining Boundary

This phase improves data quality, not nationwide coverage. It does not make a
current provincial or civil-service position table exist, bypass a restricted
official portal, or auto-publish candidates. The next expansion stage must
continue to locate current, openly accessible provincial public-institution
and civil-service tables, then import only rows that pass this gate.
