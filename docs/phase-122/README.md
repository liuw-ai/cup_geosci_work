# Phase 122: retire duplicate CNOOC API path

## Finding

The server's 2026-10-02 direct check showed that the legacy `cnooc-career`
Zhaopin API returned one row, but it was the official advertisement
`中海油服2026年校园招聘（面试信息采集）`; it contained no publishable,
current job-detail evidence. The adapter therefore failed the source run even
though the detail browser source `cnooc-career-browser` had a complete capture
(187 details, 38 open student matches on the previous run).

Treating both paths as production publishers made the health gate fail twice for
the same official platform. That did not increase coverage and could not be
fixed by weakening the student evidence gate.

## Change

- `cnooc-career` remains registered for audit and regression tests but is
  disabled for production synchronization.
- Its metadata records `cnooc-career-browser` as the replacement and preserves
  the observed failure reason.
- The organization registry now binds the China Offshore Oil campus channel to
  the detail-capture source.
- No rows were imported from the interview-collection advertisement and no
  existing student rows were rewritten.

## Acceptance

The server must run the normal source sync after deployment and show:

- `cnooc-career` absent from enabled-source failures;
- `cnooc-career-browser` still healthy with a complete capture;
- public student count unchanged unless the detail capture itself changes;
- data audit and backup checks still pass.

This phase improves production reliability; it does not claim a new COSL
vacancy. COSL remains a separate official adapter and currently reports
`success_without_matches` because the official portal has no formal open
geoscience advertisement.
