# Production Release Baseline

This document defines the smallest release contract for the employment
service. It is intentionally separate from source expansion work: a source
must not be added to production while the running Web, Worker, and browser
workers cannot be identified as one release.

## Release identity

Web and Worker receive the following release identity through the runtime
environment. Browser Workers receive the same version and Git SHA while their
own image reference is checked separately:

```text
APP_RELEASE_VERSION=<human-readable release tag>
APP_RELEASE_GIT_SHA=<exact Git commit SHA>
APP_RELEASE_IMAGE_SHA=<immutable image digest>
APP_RELEASE_BUILT_AT=<UTC ISO-8601 timestamp>
```

The application and browser Compose files use the following image variables:

```text
JOB_HUB_IMAGE=<application image tag or digest>
JOB_HUB_BROWSER_IMAGE=<browser image tag or digest>
RUNTIME_IMAGE=<same application image used as the browser base>
```

`RUNTIME_IMAGE` is important: a browser image built from a different Web/Worker
base can contain a different pipeline even when both images have the same
human-readable tag.

`GET /version` returns these four values. `GET /healthz` repeats them under
`release`. The `release-check` CLI command validates the release fields and
the three image references before a production switch.

The value `dev` or `unknown` is acceptable for local development only. A
production rollout must fail its release checklist when either the Git SHA or
image SHA is unknown.

## Required rollout order

1. Select one clean Git commit and record its SHA.
2. Build the Web/Worker image from that commit and set `JOB_HUB_IMAGE` to its
   immutable tag or digest.
3. Build the browser image from the same checkout with `RUNTIME_IMAGE` set to
   that exact application image and set `JOB_HUB_BROWSER_IMAGE` to its
   immutable tag or digest.
4. Record the resulting immutable image digests.
5. Run `python -m job_hub.cli release-check` with production variables; it must
   pass before any service is restarted.
6. Start the candidate Compose project with the release variables above.
7. Check `/version` on Web, the Worker heartbeat, and the image/environment
   identity of every browser Worker.
8. Run one capture smoke test per enabled browser worker.
9. Verify `/api/jobs`, `/api/coverage`, expiry cleanup, and a database backup.
10. Switch traffic only after all checks pass.
11. Keep the previous Compose project and database backup until the new release
   has completed one scheduled synchronization cycle.

## Scope boundary

This baseline does not split `sources.py`, `db.py`, or `cli.py` yet. Those
refactors are only safe after a single release is reproducible. Splitting
large modules before that point would make it harder to tell whether a data
change came from a code refactor or from version drift.

## Promotion gate

The next source-expansion phase may begin only when:

- Web, Worker, and every browser Worker report the same release version and
  Git SHA;
- no production service uses the `latest` tag as its only identity;
- a rollback to the previous image and database backup has been exercised;
- the release manifest records the Compose files, image digests, data-registry
  hashes, and test result.
