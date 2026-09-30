# Phase 98: Reconcile Domestic Expansion Facts

## Purpose

The domestic expansion queue is an operator planning ledger. It must agree
with the government position registry and the controlled attachment manifest;
otherwise it can direct a later worker toward an obsolete or unsuitable task.

## Official observations on 2026-09-30

- Natural Resources Ministry public API: the unauthenticated read endpoint is
  available. Its four listed recruitment batches are historical; the newest
  2026 second batch closed at `2026-04-14 17:00`. The state is therefore
  `scan_success_no_match`, not `access_limited`.
- China Geological Survey: the Development Research Center's official notice
  contains nine explicitly matched doctoral postdoctoral positions in Beijing,
  with a `2026-10-16 17:00` deadline. The queue points to two stable
  job-level identifiers (`government-position:cgs-drc-postdoc-2026-1:1` and
  `government-position:cgs-drc-postdoc-2026-9:9`); the full nine rows remain
  in the government position registry.
- China Coal Geology Group: the current mature-talent attachment was directly
  checked from the official server. It remains an official recruitment record,
  but is explicitly `current_non_student_eligible`, consistent with Phase 97.

## Contract

`current_non_student_eligible` rows must retain an observation date,
field-validation note, `student_eligible: false`, and a concrete
`student_scope_reason`. The status is neither a failed source nor a student
opportunity; it prevents a later queue consumer from attempting to publish it.
