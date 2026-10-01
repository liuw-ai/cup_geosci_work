# Phase 118: Tighten geophysics evidence boundaries

Phase 117 correctly added geophysics profiles, but its first taxonomy draft
treated several directions (`地球物理勘查`, `地球物理测井`, and similar
phrases) as exact degree names. That could publish a role whose official row
only names a technical direction, without proving that a geophysics degree is
accepted.

This follow-up keeps formal geophysics names and codes (`地球物理学`, `固体
地球物理学`, `空间物理学`, `0708/070800/070801/070802`) as exact terms and
moves technical directions to the related-review list. The existing row-level
evidence, degree and experience gates are unchanged.

Verification:

- Local full suite: `472 passed, 2 skipped`.
- Server reindex: 1,856 checked, 41 reclassified.
- Public current jobs: 154 -> 153 after removing one over-broad match.
- CNOOC browser source: 48 current explicit geophysics-compatible rows remain.
- Quality gate: pass; every published row still has official evidence URL and
  an explicit student profile match.

This is still a correctness repair, not the 75/100 source-expansion gate.
