# Phase 59: Government Evidence Revalidation and Opening Windows

## Purpose

This phase strengthens the daily publishing contract for public-institution,
postdoctoral and future civil-service position tables serving CUPB School of
Geosciences students.

It does not add jobs from search engines, Zhonggong, Huatu, public accounts,
or historical tables. Those can provide discovery leads only. Student-facing
records still require official notice, official attachment or official job
system evidence, an explicit degree/major decision, location, headcount and a
current application policy.

## Changes

1. Government position ledgers now recheck their existing official notice and
   attachment URLs on every worker sync. A static `as_of` date is only a short
   bootstrap fallback, not evidence that a role is perpetually open.
2. The new recheck result distinguishes `verified`, `source_unavailable`,
   `withdrawn` and `not_configured`. A connection, TLS, `403` or `404` failure
   is never translated into "no jobs".
3. A source is withdrawn only when the current official notice explicitly says
   that this announcement or recruitment run was cancelled. Conditional rules
   such as "if a position is cancelled" cannot remove valid jobs.
4. Revalidation reads at most 256 KiB from an HTML notice and 8 KiB from a
   registered attachment. This validates public accessibility without a daily
   full-file download.
5. The ledger supports a source-wide official application-opening date. The
   China Earthquake Administration 2027 table has 70 verified geoscience rows
   and 89 planned places, but its registration begins on 2026-10-10. It is
   retained as an audited upcoming batch and will not be shown as "open" on
   2026-09-28.

## Scope Boundary

The phase does not claim that current nationwide civil-service tables have
been published or that all 31 provincial systems are fully integrated. It
also does not treat the Beijing Municipal Human Resources and Social Security
Bureau's open Beijing Technology and Business University high-level talent
notice as a geoscience vacancy: its official table was checked and contains no
explicit target-major or unrestricted-major row.
