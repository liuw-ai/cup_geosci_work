# Phase 7.2 R4 Checkpoint: 2026-10-29

## Scope and recovery point

This checkpoint records the production state after the student discovery entry
and third-party recovery improvements. It is a factual checkpoint, not a
Phase 7.2 acceptance declaration.

- Production release: `phase7.2-20261029-r4`
- Application Git revision: `a37e690`
- Application image digest:
  `sha256:31f01ab1e2b66c317789b9f4d9e2fd392b65cc51b0be65055f395f08b5c1f282`
- Release directory: `/opt/job-hub/releases/phase7.2-20261029-r4/app`
- Previous working release and rollback target:
  `/opt/job-hub/releases/phase7.2-20261029-r3/app`
- R4 database volume is an independent copy of the R3 volume. The controlled
  government-evidence recheck created an additional verified backup:
  `/var/lib/job-hub/backups/job_hub-20261029T025613Z.sqlite3`
  (`sha256:478402bc50752a182a82cd36dacb1c2afd0e5a32492bfd8c7e7ca583c03097f3`).

The R4 image is an offline overlay of the verified local R3 application image
plus the checked-in `job_hub` code at `a37e690`. This was necessary because
the server's Debian mirror was downloading at about 21 KB/s during a clean
base-image rebuild. The exact final image digest and its R3 base are recorded
above; the full source revision remains in Git. This is recoverable but is not
a replacement for an eventually reproducible, fully pinned clean build.

## Student-facing count contract

At `2026-10-29` the server returned:

| Read path | Count | Meaning |
| --- | ---: | --- |
| `/api/jobs` | 759 | Strict current job library. It requires the existing full publication gates. |
| `/api/jobs?scope=student_discovery` | 1076 | Student discovery layer. It includes the strict library plus official opportunities with incomplete fields and labelled extension opportunities. |

The home-page profile form, category cards, and strong-relevance link now all
open the student discovery layer. The strict library remains an explicit
separate entry and its API default has not changed.

The discovery layer therefore exceeds 1,000 records, but this must not be
described as 1,076 confirmed and immediately applicable vacancies. Its current
non-additive audit dimensions are:

- `official_eligible`: 623 records;
- `official_incomplete`: 453 records;
- exact four-programme match layer: 226 records;
- broader geoscience layer: 335 records;
- incomplete-but-basic-match layer: 515 records.

Each incomplete record is retained because the official source is real and a
missing degree, location, or deadline is information the student can inspect
in the original notice. The UI must keep the missing-field warning; it must
not silently represent these records as fully verified eligibility.

## Third-party channel result

R4 adds a constrained parser for the known transparent wrapper
`link.wtturl.cn`, reading only URL-like destination parameters and then
requiring the normal registered official-host and job-detail quality gates.
This cannot publish a third-party page or a generic employer home page.

The production scan at `2026-10-29T10:31:17+08:00` found 13 Zhonggong and 7
Huatu announcement leads, but created no new private leads because they were
already known. The current lead funnel is:

| State | Count |
| --- | ---: |
| private candidate | 164 |
| official URL found | 6 |
| published after official verification | 9 |
| rejected | 2 |
| unresolved conversion status | 170 |

The replay inspected 181 leads and found 98 recovery candidates, but changed
zero leads into an official URL binding. The concrete blocker distribution was
74 `no_registered_or_government_candidate`, 46
`unregistered_official_candidate`, 32 `generic_homepage_only`, and 12
`no_candidate_links`. This means the third-party pipeline exists and is being
used for discovery, but it is not yet a large-scale official-job conversion
channel. It would be inaccurate to claim otherwise.

## Government, public institution, and civil-service result

The controlled official-evidence recheck on 2026-10-29 created the backup
listed above and checked only four declared sources:

- China Geological Survey, Gansu Geology and Mineral Bureau, and Hunan
  Geological Institute: official evidence recheck succeeded;
- Ningxia Geological Bureau: its official page returned an error document.
  This is recorded as `source_unavailable`, not as proof that its openings
  ended or remain open.

The government-position audit remains globally stale: its reviewed registry
`as_of` is `2026-09-27`, 768 hours older than the 48-hour policy. Its 157
historic reviewed rows include 36 open-until-filled records, but the stale
registry and source-specific availability checks prevent treating that number
as a new national current-job increment.

The source matrix explains why civil-service and public-institution growth has
not formed a national scale: all 31 provincial public-institution entries are
still `unlocated`; 29 provincial civil-service entries are `unlocated`, and
only 2 are `verified`. Across all five required provincial roles the matrix
contains 19 verified, 38 candidate, and 98 unlocated entries. The next correct
work is to establish and test stable official annual announcement/position-table
sources province by province, then parse the official row-level tables. A
training-site announcement, a yearly recruitment headline, or a province-wide
headcount is not a substitute for a job row.

## Remaining Phase 7.2 acceptance blockers

1. The 1,000-record discovery-layer condition is met, but the 759 strict
   current-library count remains below 1,000.
2. Third-party discovery has not achieved large-scale conversion into official
   job-level evidence.
3. National public-institution and civil-service official source coverage is
   incomplete as quantified above.
4. The government position registry is globally stale and the source-artifact
   queue still contains failed/skipped extraction, row-field mismatch, and
   lifecycle-evidence blockers.
5. Production `release-check` still fails its immutable `headless_shell`
   requirement: the server can only run `chromedp/headless-shell:latest`
   because its recorded digest cannot currently be pulled from the configured
   registry. This must remain visible until a usable immutable image is made
   available.
6. Public DNS/TLS filing, administrator credential rotation, and daily mail
   delivery are still separate production-readiness tasks.

Accordingly, Phase 7.2 is still not complete. R4 makes the student-visible
entry and its count honest, preserves rollback, and records the verified next
constraints rather than inflating job totals.
