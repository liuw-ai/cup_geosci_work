# Phase 119: Experience Evidence Gate and Production Reclassification

## Scope

This phase closes a student-facing correctness defect found during the
production review. Some official portals put the complete requirement block in
the `学历要求` field. The student publication gate previously inspected that
field for degree matching but did not inspect it for a multi-year work
experience requirement. As a result, an experienced BGP geophysicist role was
incorrectly classified as student-eligible.

## Changes

- The experience veto now reads degree-labelled evidence fields as well as
  explicit requirement fields.
- The English detector recognizes forms such as `Minimum 3+ years of
  professional ... experience`.
- Added a regression test for the exact portal layout that caused the defect.
- Added `Dockerfile.experience-gate`, a small overlay that reuses the verified
  `phase117-geophysics-overlay` image and replaces only `profiles.py`.

## Verification

Local test suite:

```text
473 passed, 2 skipped
```

Server deployment:

- overlay image: `cupb-geoscience-job-hub:phase119-experience-gate`
- web/worker containers: healthy
- database reclassification: `1856` checked, `38` reclassified
- student-visible open jobs: `152` (150 explicit matches + 2 unrestricted)
- BGP experienced role: `out_of_scope`, no longer student-visible
- backup integrity: valid
- internal production readiness: `true`
- public readiness: still `false` because the formal domain/HTTPS is not
  configured and CNPC/Sinopec remain expected access-limited browser sources.

## Coverage interpretation

This phase deliberately does not add a job count or claim the 75/100 gate.
Current visible source distribution remains concentrated in CNOOC (48), CMGB
(36), Hunan Geological Institute (34), and CGS (9). The next score-bearing
work is a new current provincial official position table and a new independent
oil-subsidiary detail source, followed by CEA activation after 2026-10-10.
