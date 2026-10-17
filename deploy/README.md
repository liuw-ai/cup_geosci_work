# Production Deployment Boundary

`compose.yaml` is the only production Compose entry point for the current
project. It is evaluated from this directory, so the build context and
versioned data mounts intentionally use `..` paths.

The root `docker-compose.yml` remains a local-development compatibility file;
it is not a production rollout command. Phase overlays, location-fix files,
and old Dockerfiles are historical artifacts and must not be combined with
this file.

A release is not approved merely because Compose parses. Before deployment,
create a Release Manifest binding the exact Git SHA, Compose SHA-256, image
digests, configuration SHA-256, schema version, and verified database backup.
This Phase 3 work does not authorize a server deployment.
