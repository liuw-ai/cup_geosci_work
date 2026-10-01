# Phase 131: government evidence freshness and provincial recheck gate

## Scope

This phase closes a reporting defect before adding more public vacancies. A
reviewed position ledger can be older than its freshness window. The public
publication gate already withdraws those rows, but the quality report did not
identify the affected sources as pending revalidation. That made a stale
ledger look like a successful scan with no matching jobs.

## Changes

- `government_position_quality_report` now emits
  `pending_evidence_sources` for eligible rows whose official evidence is
  missing, stale, or awaiting a fresh check.
- `source_failures_or_pending` includes those source-level pending states while
  avoiding double-counting explicit `source_unavailable` and manual-confirmation
  states.
- The report interpretation now says that stale or unverified sources cannot
  be read as having no vacancies.
- Student-facing lifecycle behavior is unchanged: pending evidence remains
  private and does not publish old rows.

## Read-only official recheck

On 2026-10-02, a direct-network read-only probe checked the already registered
official notice and attachment URLs for the current provincial rows. It did not
discover new URLs or modify the registry/database.

| Source | Result | Meaning |
| --- | --- | --- |
| 安徽省地质矿产勘查局 | verified | 2 registered official URLs reachable |
| 甘肃省地质矿产勘查开发局 | verified | 2 registered official URLs reachable |
| 湖北省自然资源厅 | verified | 2 registered official URLs reachable |
| 湖南省地质院 | verified | 2 registered official URLs reachable |
| 宁夏地质局 | verified | 4 registered official URLs reachable |
| 中国地质调查局 | verified | 1 registered official URL reachable |
| 中国地震局 2027 | manual confirmation required | automated attachment retrieval remains disabled by policy |

The probe is not counted as a production refresh. Two successful server-side
refresh events are still required before this phase can promote any new source
or claim continuous availability.

## Existing Sinopec capture audit

The 2026-09-29 official SPA snapshot itself is structurally complete when
validated by the legacy Sinopec adapter: 132/132 units, 35/35 keyword
candidate units, 363 job rows, zero detail failures, zero row-count
mismatches, and 81 rows containing explicit geoscience markers. Every row has
non-empty title, employer, major, degree, location, deadline, and an official
`job.sinopec.com` detail URL. This is useful adapter evidence, but its capture
timestamp is outside the 30-hour production freshness window on 2026-10-02;
it must not be re-published as current. The browser worker must produce a new
successful capture before these rows can return to the student-facing path.

## Verification

- Government position tests: `20 passed`.
- Full local suite: `490 passed, 2 skipped` after the new regression is run.
- The stale-ledger regression proves that a stale snapshot produces a pending
  source signal and zero public rows.

## Gate status

Phase 131 is **partially complete**. The report defect is fixed and the
official URLs are reachable from this development network, but server-side
continuous refresh evidence is still missing. No new vacancy was imported or
activated from a static snapshot.

## Next phase

1. Run the same recheck on the cloud worker and persist two immutable success
   events for one independent provincial source.
2. Compare attachment fingerprints and row-level fields before retaining the
   existing snapshot.
3. Only after that gate, add the next independent province or current official
   position table.
4. In parallel, run a fresh complete Sinopec browser capture; the existing
   132-unit/35-candidate snapshot is evidence of the adapter, not a current
   publication snapshot.
