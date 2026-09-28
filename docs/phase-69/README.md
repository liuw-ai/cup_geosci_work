# Phase 69: Upcoming Official Position Preview

## Objective

Expose verified government and public-institution positions whose official
registration window has not opened yet, without mixing them into the current
student-facing vacancy count.

## Delivered

- Added `upcoming_position_records()` with the same source-freshness,
  professional-match, deadline, and official opening-date gates as current
  publication.
- Added `/government/upcoming` and a clearly labelled home-page preview.
- Kept every row linked to its official notice and attachment URL, including
  position code, major, degree, location, and headcount.
- Added regression coverage proving the 70 China Earthquake Administration
  rows remain separate from current open rows until `2026-10-10`.

This phase does not claim new current vacancies. It makes already verified
future opportunities useful to students while preserving the publication
boundary.
