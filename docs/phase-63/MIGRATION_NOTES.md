# Phase 63 Migration Notes

No database schema migration is required. The persistent SQLite database stays
on the server volume and is not copied into Git.

The operational migration is source-control recovery: the production release's
reviewed static registries are now versioned locally. Rollback remains possible
through the prior server release directories and the Git tag created for this
phase.
