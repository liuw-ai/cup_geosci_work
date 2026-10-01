# Phase 114: Current subsidiary and government-source audit

## Scope

This phase rechecked the unfinished expansion gates from phase 113 on the
production server. The purpose was to determine whether the newly scanned
Three-Barrel-Oil subsidiaries or official government attachments contain a
current, student-eligible geoscience row. A discovered row was not promoted
unless the official detail/attachment simultaneously proved the job, major,
degree, location, application lifecycle and student eligibility.

## Server evidence

The first audit on 2026-10-01 recorded 225 runs. After the scheduled worker
cycle completed, the final ledger contained 253 runs; the additional 28 runs
were source refreshes and attachment checks, not fabricated vacancies. The
current public snapshot remains 146 jobs. The worker heartbeat is healthy and CNOOC's
browser detail capture completed 343/343 details with 46 explicit student
matches.

The government position audit found 157 reviewed rows in the versioned
registry:

- 52 currently publishable explicit government rows;
- 91 China Earthquake Administration rows scheduled for 2026-10-10;
- 14 closed rows retained only for historical evidence and parser regression;
- 0 source verification failures in the audit result.

The scheduled CEA rows were deliberately not published before their official
opening date. The registry itself is still dated 2026-09-27 and therefore is
not treated as a fresh scan merely because the source verification table is
healthy.

## Subsidiary review decisions

The following official details were re-read and remain outside the student
publication gate:

| Source | Evidence result | Decision |
| --- | --- | --- |
| CNPC BGP | `Seismic Data Processing Geophysicist (Experienced)`, geophysics/ petroleum geology, bachelor or above, minimum 3 years experience | Keep internal; not an in-school graduate vacancy |
| CMGB Inner Mongolia | geology/hydrology/environment geology roles, 2+ years experience; technical manager roles require 3-5 years | Keep internal; experience gate blocks student publication |
| Sinopec PEPRIS | official rows have expired 2025-11-15 deadline; several rows lack row-level major evidence | Keep historical/private; do not revive or infer current vacancies |
| COSL | no current row with complete student-eligible official detail evidence | Keep source run as successful-without-match |

This confirms that the source adapters are finding real subsidiary material;
the remaining bottleneck is the availability of current graduate-eligible
official rows, not a permission to relax the publication gate.

## Acceptance decision

The phase does **not** claim 75/100 and does not increase the public count.
The honest score remains approximately **73/100**. This phase closes the
manual-review ambiguity for the scanned subsidiary rows and records that
failed/experienced/expired rows must not be counted as current vacancies.

## Next acceptance work

1. On 2026-10-10, re-fetch and hash the China Earthquake Administration
   announcement and attachment, then activate only rows whose opening window
   is confirmed by the official source.
2. Revalidate the current Anhui and Gansu tables after their next official
   scan; a transport error must remain a source failure, never an empty scan.
3. Continue the subsidiary queue with a current graduate-eligible detail
   chain, prioritizing CNPC/CNOOC/Sinopec units whose official notices expose a
   full row-level major and degree block.
4. Re-run `coverage`, `government-position-audit`, `source-run-ledger`,
   `worker-health` and `production-readiness` after each accepted change.
