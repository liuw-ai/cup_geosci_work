# Phase 64: Government Position Lineage Correction

## Purpose

This phase corrects two student-facing government-position defects found by
comparing the reviewed official ledger with the production database:

1. an existing attachment-candidate identifier was replaced during registry
   reconciliation and then treated as missing, so a current official Anhui
   geoscience role was withdrawn;
2. official second-level disciplines and code-defined directions under the
   CUPB target first-level disciplines were incorrectly treated as unrelated.

The same audited taxonomy correction exposes one previously omitted student
match in the frozen Sinopec capture: Huadong Petroleum Bureau's doctoral oil
and gas exploration research role explicitly names `矿产普查与勘探` (the
`081801` direction under `0818`). It is included because of that named
discipline, not because its title contains an oil-and-gas keyword.

The audit also corrected a shared degree parser defect: `博士研究生` had been
mistakenly interpreted as both master's and doctoral eligibility because it
contains the generic word `研究生`. A doctoral-only role now remains visible
only to the matching doctoral profile.

## Scope

The taxonomy now explicitly recognizes only named, code-defined directions
within the requested programs:

- `0709` geology directions, including mineralogy, petrology, ore deposits,
  geochemistry, paleontology and stratigraphy, structural geology, and
  Quaternary geology;
- `0818` geological resources and geological engineering directions, including
  mineral prospecting and exploration and geophysical exploration and
  information technology;
- the code-defined resource exploration and geological engineering programs.

Generic wording such as `相关专业`, broad industry keywords, and adjacent
non-geoscience fields remain insufficient for public publication.

## Reconciliation Invariant

When a reviewed registry row matches an existing attachment-derived job, the
worker now reconciles using the preserved external identifier actually saved in
the database. This prevents a valid job from being marked withdrawn after a
successful evidence update.

The China Geological Survey development-research-center notice also exposed an
official-site TLS defect: the `www.drc.cgs.gov.cn` HTTPS certificate does not
cover that host in the server environment. The verified official HTTP page
returns the reviewed original content and is used as the evidence endpoint;
the cause is recorded in the ledger and normal daily freshness withdrawal still
applies if it later becomes unavailable.

## Expected Production Result

After deployment and one normal worker cycle, the two current Anhui doctoral
roles and four current, explicitly code-matched Hunan/CGS roles must remain or
become student-visible only when their official evidence and deadline gates are
still current.
