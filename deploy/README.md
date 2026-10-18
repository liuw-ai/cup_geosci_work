# Production Deployment Boundary

`compose.yaml` is the only production Compose entry point for the current
project. It is evaluated from this directory, so the build context and
versioned data mounts intentionally use `..` paths.

The application build is explicitly pinned to
`deploy/docker/Dockerfile.app`, and the browser-worker build input is kept at
`deploy/docker/Dockerfile.browser`. Their SHA-256 values are recorded in the
Phase 4 Manifest. The root Dockerfiles remain local-compatibility artifacts
until the old development compose contract is retired; they are not
production build inputs.

The root `docker-compose.yml` remains a local-development compatibility file;
it is not a production rollout command. Phase overlays, location-fix files,
and old Dockerfiles are historical artifacts and must not be combined with
this file.

A release is not approved merely because Compose parses. Before deployment,
create a Release Manifest binding the exact Git SHA, Compose SHA-256, image
digests, configuration SHA-256, schema version, and verified database backup.
This Phase 4 local work does not authorize a server deployment. A server rollout still
requires a clean local review and explicit user approval after the third-party evidence
and three-oil entry evidence blockers are resolved.
