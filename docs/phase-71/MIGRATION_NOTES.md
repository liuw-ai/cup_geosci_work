# Phase 71 Migration Notes

No database schema migration is required. The new image only supplies the
Playwright Python client to the isolated browser workers; the capture JSON,
heartbeat tables, publication gate, and job data remain unchanged.

On a server that already has the ordinary application image, build it first so
`Dockerfile.browser` can use `cupb-geoscience-job-hub:latest` as its base. Keep
the existing `job_hub_data` volume. Do not delete or recreate that volume.
