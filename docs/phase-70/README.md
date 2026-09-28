# Phase 70: Publish-Gate Recovery and Derived-Field Reindex

## Objective

Keep the daily publication gate reliable when a source process is interrupted,
and ensure existing official rows are re-evaluated whenever the professional
taxonomy or publication rules change.

## Delivered

- Recover only crawl runs older than `CRAWL_RUN_STALE_SECONDS` immediately
  before the daily audit. A recent running scan still blocks publication.
- Recompute persisted taxonomy, degree, relevance and student-publication
  fields after every worker source cycle. This repairs stale decisions without
  redownloading an unchanged official source.
- Corrected the Phase 69 report to use the post-worker production count of 197
  student-visible jobs, not the pre-reconciliation estimate of 330.

This phase does not relax the professional gate and does not add synthetic or
unverified jobs.
