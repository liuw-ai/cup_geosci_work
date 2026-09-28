# Phase 63: Production Release Reconciliation

## Purpose

The local working tree did not contain the reviewed source registries,
position-table batches, parser fixes, tests and documentation present in the
running server release, despite the branch pointer being unchanged. Deploying
from that stale working tree would have replaced the production ledger with an
incomplete copy.

This phase restores the local working tree from the server release and verifies
that it matches the version-controlled baseline before any further source
expansion. It is a release-integrity correction, not a job count increase.

## Scope

- restored the tracked project contents from `/opt/cup_geosci_releases/95b0997`;
- excluded server-only `.env`, the persistent database volume, runtime caches,
  and local temporary research files;
- restored the reviewed government position-table registry and its official
  evidence batches;
- retained the existing student publication gate and private review workflow.

## Safety Invariant

The checked-in registry and the production release registry have the same
SHA-256 digest:

```text
91cb6509a102aa29e337a7363d599d2177340c6342b1470f8963d2adbf986e23
```

Future releases must be built from a Git commit and must compare this digest,
or an intentionally reviewed successor, before deployment.

## Next Input

Coverage expansion resumes only from current official notices and position
tables. Each new row still needs official notice, attachment/detail evidence,
an explicit eligible major (or unrestricted-major), degree, location,
headcount and current deadline before it can reach students.
