from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from job_hub.contracts import (
    OFFICIAL_EVIDENCE_TYPES,
    OFFICIAL_DOMAIN_STATUSES,
    is_http_url,
    validate_artifact_candidate,
    validate_artifact_candidate_transition,
    validate_candidate_lead_transition,
    validate_job_evidence,
    validate_source_artifact,
    validate_source_record,
)
from job_hub.employers import enrich_job
from job_hub.official_identity import official_detail_key
from job_hub.policy_contract import publication_policy_digest
from job_hub.phase3_policy import (
    PUBLICATION_LAYERS,
    SOURCE_AVAILABILITY_STATES,
    WITHDRAWAL_REASONS,
    derive_publication_layer,
    no_deadline_expiry,
    normalize_publication_layer,
    normalize_trust_grade,
)
from job_hub.profiles import (
    PUBLIC_STUDENT_PUBLICATION_STATUSES,
    evaluate_student_publication,
)
from job_hub.discovery import (
    lead_fingerprint,
    normalize_lead_url,
    official_domain_assessment,
)
from zoneinfo import ZoneInfo


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    publisher TEXT NOT NULL,
    homepage_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    category TEXT NOT NULL,
    source_tier TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL DEFAULT '{}',
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT REFERENCES sources(id),
    external_id TEXT,
    fingerprint TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    employer TEXT NOT NULL,
    group_name TEXT,
    category TEXT NOT NULL,
    source_tier TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    application_url TEXT,
    location TEXT,
    canonical_employer_id TEXT,
    canonical_employer_name TEXT,
    parent_employer_name TEXT,
    province TEXT,
    provinces_json TEXT NOT NULL DEFAULT '[]',
    city TEXT,
    country_or_region TEXT,
    location_confidence TEXT NOT NULL DEFAULT 'unknown',
    location_evidence TEXT,
    verification_status TEXT NOT NULL DEFAULT 'published_official',
    official_evidence_url TEXT,
    official_detail_key TEXT,
    official_detail_precedence INTEGER NOT NULL DEFAULT 100,
    published_date TEXT,
    deadline_date TEXT,
    degree_levels_json TEXT NOT NULL DEFAULT '[]',
    major_tags_json TEXT NOT NULL DEFAULT '[]',
    field_evidence_json TEXT NOT NULL DEFAULT '{}',
    publication_status TEXT NOT NULL DEFAULT 'pending_evidence',
    publication_basis_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    relevance_score INTEGER NOT NULL DEFAULT 0,
    relevance_band TEXT NOT NULL DEFAULT '拓展机会',
    status TEXT NOT NULL DEFAULT 'open',
    superseded_successor_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    superseded_reason TEXT NOT NULL DEFAULT '',
    superseded_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_deadline
ON jobs(status, deadline_date);

CREATE INDEX IF NOT EXISTS idx_jobs_category
ON jobs(category);

CREATE INDEX IF NOT EXISTS idx_jobs_relevance
ON jobs(relevance_score DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_updated
ON jobs(updated_at DESC);

CREATE TABLE IF NOT EXISTS source_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    parent_url TEXT NOT NULL,
    artifact_url TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    media_type TEXT,
    content_sha256 TEXT,
    storage_path TEXT,
    parser_version TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'registered',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_id, artifact_url)
);

CREATE INDEX IF NOT EXISTS idx_source_artifacts_source_status
ON source_artifacts(source_id, extraction_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS source_artifact_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id INTEGER NOT NULL REFERENCES source_artifacts(id) ON DELETE CASCADE,
    row_key TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    row_kind TEXT NOT NULL DEFAULT 'tabular',
    cells_json TEXT NOT NULL DEFAULT '{}',
    row_text TEXT NOT NULL DEFAULT '',
    extraction_confidence TEXT NOT NULL DEFAULT 'high',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(artifact_id, row_key)
);

CREATE INDEX IF NOT EXISTS idx_source_artifact_rows_artifact
ON source_artifact_rows(artifact_id, sheet_name, row_number);

CREATE TABLE IF NOT EXISTS artifact_job_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_row_id INTEGER NOT NULL UNIQUE
        REFERENCES source_artifact_rows(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    candidate_key TEXT NOT NULL UNIQUE,
    official_page_url TEXT NOT NULL,
    title TEXT NOT NULL,
    employer TEXT NOT NULL,
    application_url TEXT,
    location TEXT,
    published_date TEXT,
    deadline_date TEXT,
    degree_levels_json TEXT NOT NULL DEFAULT '[]',
    major_tags_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    field_evidence_json TEXT NOT NULL DEFAULT '{}',
    relevance_score INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    review_note TEXT NOT NULL DEFAULT '',
    published_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifact_job_candidates_status
ON artifact_job_candidates(review_status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_artifact_job_candidates_source
ON artifact_job_candidates(source_id, review_status);

CREATE TABLE IF NOT EXISTS job_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    artifact_id INTEGER REFERENCES source_artifacts(id) ON DELETE SET NULL,
    evidence_key TEXT NOT NULL UNIQUE,
    evidence_type TEXT NOT NULL,
    field_name TEXT NOT NULL DEFAULT 'job_record',
    evidence_url TEXT NOT NULL,
    locator TEXT,
    excerpt TEXT,
    verification_status TEXT NOT NULL DEFAULT 'verified',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_evidence_job_status
ON job_evidence(job_id, verification_status, evidence_type);

CREATE INDEX IF NOT EXISTS idx_job_evidence_artifact
ON job_evidence(artifact_id);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_job_events_date
ON job_events(occurred_at DESC);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT REFERENCES sources(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    open_matching_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    outcome TEXT NOT NULL DEFAULT 'unknown',
    attempts INTEGER NOT NULL DEFAULT 0,
    retryable_failures INTEGER NOT NULL DEFAULT 0,
    transport_mode TEXT NOT NULL DEFAULT 'environment',
    evidence_complete_count INTEGER NOT NULL DEFAULT 0,
    manual_review_count INTEGER NOT NULL DEFAULT 0,
    attachment_success_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS service_heartbeats (
    service_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reports (
    report_date TEXT PRIMARY KEY,
    published_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    delivery_error TEXT
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    last_success_at TEXT,
    status_code INTEGER,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_source_health_status
ON source_health(status, checked_at DESC);

-- Student-facing reads must be able to enforce the same capture-freshness
-- contract as the worker. ``source_health.last_success_at`` is deliberately
-- not used here: it records when a sync finished and can therefore be newer
-- than an old snapshot that was merely re-read. This table stores the
-- evidence capture time from a complete successful source run.
CREATE TABLE IF NOT EXISTS source_capture_freshness (
    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    crawl_run_id INTEGER,
    evidence_kind TEXT NOT NULL DEFAULT 'capture_manifest'
);

CREATE INDEX IF NOT EXISTS idx_source_capture_freshness_captured
ON source_capture_freshness(captured_at DESC);

-- Government position ledgers are reviewed source snapshots. Their source
-- evidence is rechecked independently of ordinary crawl runs so a static JSON
-- ``as_of`` date cannot be mistaken for a perpetual live scan.
CREATE TABLE IF NOT EXISTS government_source_verifications (
    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    last_success_at TEXT,
    availability_confirmed_at TEXT,
    availability_confirmation_detail TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    evidence_fingerprint TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_government_source_verifications_status
ON government_source_verifications(status, checked_at DESC);

-- Keep an immutable history of government evidence checks.  The current
-- verification table is intentionally a latest-state projection used by the
-- publication gate; this event table is the audit trail needed to prove that
-- a source was refreshed repeatedly rather than merely read once.
CREATE TABLE IF NOT EXISTS government_source_verification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    last_success_at TEXT,
    availability_confirmed_at TEXT,
    availability_confirmation_detail TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    evidence_fingerprint TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_government_source_verification_events_source
ON government_source_verification_events(source_id, checked_at DESC);

-- A durable per-source queue keeps one slow or blocked domain from stopping
-- the rest of the nationwide scan.  The queue is operational state, not job
-- content, and can be rebuilt from the versioned source registry.
CREATE TABLE IF NOT EXISTS source_tasks (
    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    lease_until TEXT,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_error TEXT,
    last_error_class TEXT,
    last_run_id INTEGER,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_tasks_due
ON source_tasks(status, next_attempt_at, priority);

CREATE TABLE IF NOT EXISTS coverage_snapshots (
    snapshot_date TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    open_jobs INTEGER NOT NULL,
    explicit_job_profile_matches INTEGER NOT NULL,
    review_job_profile_matches INTEGER NOT NULL,
    students_with_explicit_match INTEGER NOT NULL,
    successful_scan_sources INTEGER NOT NULL,
    sources_with_open_matches INTEGER NOT NULL,
    source_top_share REAL NOT NULL,
    category_top_share REAL NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_coverage_snapshots_captured
ON coverage_snapshots(captured_at DESC);

CREATE TABLE IF NOT EXISTS candidate_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_provider TEXT NOT NULL,
    lead_url TEXT NOT NULL,
    title TEXT NOT NULL,
    employer_hint TEXT,
    location_hint TEXT,
    province_hint TEXT,
    published_date TEXT,
    official_url TEXT,
    discovery_source_id TEXT,
    lead_fingerprint TEXT,
    official_source_id TEXT,
    official_domain_status TEXT NOT NULL DEFAULT 'unverified',
    official_checked_at TEXT,
    official_application_url TEXT,
    verification_status TEXT NOT NULL DEFAULT 'candidate',
    verification_note TEXT NOT NULL DEFAULT '',
    published_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_leads_status
ON candidate_leads(verification_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS candidate_lead_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES candidate_leads(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    note TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_lead_events_lead
ON candidate_lead_events(lead_id, occurred_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS candidate_lead_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES candidate_leads(id) ON DELETE CASCADE,
    discovery_source_id TEXT,
    lead_url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(lead_id, discovery_source_id, normalized_url)
);

CREATE INDEX IF NOT EXISTS idx_candidate_lead_mentions_lead
ON candidate_lead_mentions(lead_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_candidate_lead_mentions_source
ON candidate_lead_mentions(discovery_source_id, captured_at DESC);

    -- Phase 3 keeps a separate, immutable decision record for historical
    -- withdrawals.  This prevents a legacy "not seen in one scan" event from
    -- being silently treated as valid current withdrawal evidence.
    CREATE TABLE IF NOT EXISTS phase3_withdrawal_audits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
        legacy_reason TEXT NOT NULL DEFAULT '',
        classification TEXT NOT NULL,
        eligible_withdrawal INTEGER NOT NULL DEFAULT 0,
        recommended_status TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        reviewed INTEGER NOT NULL DEFAULT 0,
        reviewed_by TEXT,
        reviewed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_phase3_withdrawal_audits_classification
    ON phase3_withdrawal_audits(classification, eligible_withdrawal, reviewed);
"""


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


_ACTIVE_TRANSACTION: ContextVar[tuple[Path, sqlite3.Connection] | None] = ContextVar(
    "job_hub_active_transaction", default=None
)

# Browser workers start together and each bootstraps the shared source
# registry.  A five-second busy window is too short for that legitimate
# startup burst and turns a recoverable SQLite writer queue into a failed
# capture.  Keep this explicit and below the connection timeout so reads and
# writes wait for the current transaction instead of racing or failing fast.
SQLITE_BUSY_TIMEOUT_MS = 30_000


class _ClosingConnection(sqlite3.Connection):
    """Make ``with database.connect()`` release SQLite handles predictably.

    sqlite3's built-in context manager commits or rolls back but leaves the
    connection open. This repository uses the idiom in many read paths; a
    closing context prevents stale WAL handles from blocking a maintenance-mode
    backup restore, particularly on Windows.
    """

    def __exit__(self, *args: object) -> bool:
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path
        self.read_only = read_only

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            # Read-only reports must not switch journal mode or otherwise
            # acquire a writer lock while workers are bootstrapping sources.
            # Use SQLite's URI mode so a typo or missing database fails
            # clearly instead of silently creating a new empty file.
            database_uri = f"{self.path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(
                database_uri,
                uri=True,
                timeout=30,
                detect_types=sqlite3.PARSE_DECLTYPES,
                factory=_ClosingConnection,
            )
        else:
            connection = sqlite3.connect(
                self.path,
                timeout=30,
                detect_types=sqlite3.PARSE_DECLTYPES,
                factory=_ClosingConnection,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not self.read_only:
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            self._migrate_schema(connection)
            connection.commit()
        finally:
            # sqlite3's connection context manager commits or rolls back but
            # does not close the handle. Leaving it open can retain a WAL lock
            # on Windows and makes an explicitly confirmed restore impossible.
            connection.close()

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        """Apply additive migrations without rewriting public job records.

        SQLite supports the small, non-destructive column additions needed by
        this project.  Existing jobs gain only an evidence index row when they
        already contain a valid official link; reports and crawl history remain
        untouched.
        """
        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        additions = {
            "canonical_employer_id": "TEXT",
            "canonical_employer_name": "TEXT",
            "parent_employer_name": "TEXT",
            "province": "TEXT",
            "provinces_json": "TEXT NOT NULL DEFAULT '[]'",
            "city": "TEXT",
            "country_or_region": "TEXT",
            "location_confidence": "TEXT NOT NULL DEFAULT 'unknown'",
            "location_evidence": "TEXT",
            "verification_status": "TEXT NOT NULL DEFAULT 'published_official'",
            "official_evidence_url": "TEXT",
            "official_detail_key": "TEXT",
            "official_detail_precedence": "INTEGER NOT NULL DEFAULT 100",
            "field_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
            "publication_status": "TEXT NOT NULL DEFAULT 'pending_evidence'",
            "publication_basis_json": "TEXT NOT NULL DEFAULT '{}'",
            # Phase 3 evidence and publication contract.  These are independent
            # columns so audits and read gates never have to interpret metadata.
            "trust_grade": "TEXT NOT NULL DEFAULT 'A'",
            "source_availability_status": "TEXT NOT NULL DEFAULT 'available'",
            "source_unavailable_reason": "TEXT NOT NULL DEFAULT ''",
            "publication_layer": "TEXT NOT NULL DEFAULT 'not_public'",
            "publication_layer_before_unavailable": "TEXT",
            "third_party_table_url": "TEXT",
            "official_table_url": "TEXT",
            "official_notice_url": "TEXT",
            "official_application_url": "TEXT",
            "table_sha256": "TEXT",
            "table_sheet_name": "TEXT",
            "table_row_number": "INTEGER",
            "position_code": "TEXT",
            "withdrawal_reason": "TEXT",
            "withdrawal_evidence_url": "TEXT",
            "last_official_verified_at": "TEXT",
            "no_deadline_expires_at": "TEXT",
            "superseded_successor_job_id": "INTEGER REFERENCES jobs(id) ON DELETE SET NULL",
            "superseded_reason": "TEXT NOT NULL DEFAULT ''",
            "superseded_at": "TEXT",
        }
        added_official_evidence_url = False
        for name, definition in additions.items():
            if name not in existing_columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
                added_official_evidence_url = (
                    added_official_evidence_url or name == "official_evidence_url"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_publication_status "
            "ON jobs(status, publication_status, deadline_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_phase3_publication "
            "ON jobs(status, publication_layer, source_availability_status, deadline_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_phase3_source "
            "ON jobs(source_id, trust_grade, last_official_verified_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_superseded_lineage "
            "ON jobs(status, superseded_successor_job_id, superseded_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS phase3_withdrawal_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                legacy_reason TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL,
                eligible_withdrawal INTEGER NOT NULL DEFAULT 0,
                recommended_status TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                reviewed INTEGER NOT NULL DEFAULT 0,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_phase3_withdrawal_audits_classification "
            "ON phase3_withdrawal_audits(classification, eligible_withdrawal, reviewed)"
        )
        source_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(sources)").fetchall()
        }
        source_additions = {
            "trust_grade": "TEXT NOT NULL DEFAULT 'A'",
            "batch_candidate_allowed": "INTEGER NOT NULL DEFAULT 0",
            "discovery_policy": "TEXT NOT NULL DEFAULT 'official_only'",
        }
        for name, definition in source_additions.items():
            if name not in source_columns:
                connection.execute(f"ALTER TABLE sources ADD COLUMN {name} {definition}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sources_phase3_trust "
            "ON sources(trust_grade, batch_candidate_allowed, enabled)"
        )
        lead_phase3_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(candidate_leads)").fetchall()
        }
        lead_phase3_additions = {
            "trust_grade": "TEXT NOT NULL DEFAULT 'D'",
            "official_table_url": "TEXT",
            "third_party_table_url": "TEXT",
            "official_application_url": "TEXT",
            "conversion_status": "TEXT NOT NULL DEFAULT 'unresolved'",
            "conversion_checked_at": "TEXT",
        }
        for name, definition in lead_phase3_additions.items():
            if name not in lead_phase3_columns:
                connection.execute(
                    f"ALTER TABLE candidate_leads ADD COLUMN {name} {definition}"
                )
        crawl_run_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(crawl_runs)").fetchall()
        }
        if "open_matching_count" not in crawl_run_columns:
            connection.execute(
                "ALTER TABLE crawl_runs "
                "ADD COLUMN open_matching_count INTEGER NOT NULL DEFAULT 0"
            )
        crawl_run_additions = {
            "outcome": "TEXT NOT NULL DEFAULT 'unknown'",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "retryable_failures": "INTEGER NOT NULL DEFAULT 0",
            "transport_mode": "TEXT NOT NULL DEFAULT 'environment'",
            "evidence_complete_count": "INTEGER NOT NULL DEFAULT 0",
            "manual_review_count": "INTEGER NOT NULL DEFAULT 0",
            "attachment_success_count": "INTEGER NOT NULL DEFAULT 0",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, definition in crawl_run_additions.items():
            if name not in crawl_run_columns:
                connection.execute(
                    f"ALTER TABLE crawl_runs ADD COLUMN {name} {definition}"
                )
        source_task_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(source_tasks)").fetchall()
        }
        source_task_additions = {
            "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in source_task_additions.items():
            if name not in source_task_columns:
                connection.execute(
                    f"ALTER TABLE source_tasks ADD COLUMN {name} {definition}"
                )
        # Rolling "招满即止" recruitment has a different proof obligation from
        # a normal notice/attachment refresh.  Keep the latest manual current-
        # vacancy confirmation separately so an automatic evidence recheck
        # cannot accidentally renew that stronger assertion.
        government_verification_additions = {
            "availability_confirmed_at": "TEXT",
            "availability_confirmation_detail": "TEXT NOT NULL DEFAULT ''",
        }
        for table_name in (
            "government_source_verifications",
            "government_source_verification_events",
        ):
            verification_columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            for name, definition in government_verification_additions.items():
                if name not in verification_columns:
                    connection.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"
                    )
        lead_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(candidate_leads)").fetchall()
        }
        lead_additions = {
            "discovery_source_id": "TEXT",
            "lead_fingerprint": "TEXT",
            "official_source_id": "TEXT",
            "official_domain_status": "TEXT NOT NULL DEFAULT 'unverified'",
            "official_checked_at": "TEXT",
        }
        for name, definition in lead_additions.items():
            if name not in lead_columns:
                connection.execute(
                    f"ALTER TABLE candidate_leads ADD COLUMN {name} {definition}"
                )
        legacy_leads = connection.execute(
            "SELECT id, lead_url, title, employer_hint, lead_fingerprint "
            "FROM candidate_leads WHERE lead_fingerprint IS NULL OR lead_fingerprint = ''"
        ).fetchall()
        for lead in legacy_leads:
            connection.execute(
                "UPDATE candidate_leads SET lead_fingerprint = ? WHERE id = ?",
                (
                    lead_fingerprint(
                        str(lead["lead_url"]),
                        title=str(lead["title"] or ""),
                        employer_hint=str(lead["employer_hint"] or ""),
                    ),
                    int(lead["id"]),
                ),
            )
        # These indexes must be created after the additive columns above.  An
        # existing Phase 1 database may have the candidate_leads table without
        # the Phase 5 columns when executescript() starts.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_leads_discovery_source "
            "ON candidate_leads(discovery_source_id, verification_status, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_leads_fingerprint "
            "ON candidate_leads(lead_fingerprint)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_province ON jobs(province, status)"
        )
        legacy_province_rows = connection.execute(
            "SELECT id, province FROM jobs "
            "WHERE (provinces_json IS NULL OR provinces_json = '[]') "
            "AND province IS NOT NULL AND province != ''"
        ).fetchall()
        if legacy_province_rows:
            connection.executemany(
                "UPDATE jobs SET provinces_json = ? WHERE id = ?",
                [
                    (json.dumps([str(row["province"])], ensure_ascii=False), int(row["id"]))
                    for row in legacy_province_rows
                ],
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_canonical_employer "
            "ON jobs(canonical_employer_id, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_official_detail "
            "ON jobs(official_detail_key, official_detail_precedence, status)"
        )
        if added_official_evidence_url:
            connection.execute(
                "UPDATE jobs SET official_evidence_url = source_url "
                "WHERE official_evidence_url IS NULL OR official_evidence_url = ''"
            )
        else:
            connection.execute(
                "UPDATE jobs SET official_evidence_url = source_url "
                "WHERE (official_evidence_url IS NULL OR official_evidence_url = '') "
                "AND source_url IS NOT NULL AND source_url != ''"
            )
        # Previous releases already kept direct 国聘 detail links in their
        # source/evidence fields. Backfill only this narrow, URL-proven global
        # identity; no historical row is merged, deleted or reclassified.
        identity_rows = connection.execute(
            """
            SELECT id, source_url, application_url, official_evidence_url,
                   field_evidence_json
            FROM jobs
            WHERE official_detail_key IS NULL OR official_detail_key = ''
            """
        ).fetchall()
        for row in identity_rows:
            try:
                evidence = json.loads(str(row["field_evidence_json"] or "{}"))
            except (TypeError, ValueError):
                evidence = {}
            key = official_detail_key(
                source_url=row["source_url"],
                application_url=row["application_url"],
                official_evidence_url=row["official_evidence_url"],
                field_evidence=evidence if isinstance(evidence, dict) else None,
            )
            if key is not None:
                connection.execute(
                    "UPDATE jobs SET official_detail_key = ? WHERE id = ?",
                    (key, int(row["id"])),
                )
        Database._backfill_job_evidence(connection)
        Database._backfill_candidate_lead_events(connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active_transaction = _ACTIVE_TRANSACTION.get()
        database_path = self.path.resolve()
        if (
            active_transaction is not None
            and active_transaction[0] == database_path
        ):
            # Reuse the outer connection so compound operations can commit or
            # roll back as one unit without opening a nested SQLite transaction.
            yield active_transaction[1]
            return
        connection = self.connect()
        token = _ACTIVE_TRANSACTION.set((database_path, connection))
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            _ACTIVE_TRANSACTION.reset(token)
            connection.close()

    def upsert_source(self, source: dict[str, Any]) -> None:
        source = validate_source_record(source)
        now = utc_now()
        payload = json.dumps(source.get("config", {}), ensure_ascii=False)
        with self.transaction() as connection:
            # Some sources are deliberately switched by a guarded production
            # transition.  Their registry definition describes the default for
            # a new database, while the durable enabled state records whether
            # the transition has actually passed in this deployment.  Without
            # this preservation a container restart could silently reactivate a
            # retired snapshot or disable a validated dynamic source.
            existing = connection.execute(
                "SELECT enabled FROM sources WHERE id = ?", (source["id"],)
            ).fetchone()
            enabled = int(source.get("enabled", True))
            if existing is not None and source.get("config", {}).get(
                "runtime_enabled_control"
            ):
                enabled = int(existing["enabled"])
            connection.execute(
                """
                INSERT INTO sources (
                    id, name, publisher, homepage_url, source_type, category,
                    source_tier, enabled, config_json, trust_grade,
                    batch_candidate_allowed, discovery_policy, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    publisher=excluded.publisher,
                    homepage_url=excluded.homepage_url,
                    source_type=excluded.source_type,
                    category=excluded.category,
                    source_tier=excluded.source_tier,
                    enabled=excluded.enabled,
                    config_json=excluded.config_json,
                    trust_grade=excluded.trust_grade,
                    batch_candidate_allowed=excluded.batch_candidate_allowed,
                    discovery_policy=excluded.discovery_policy,
                    updated_at=excluded.updated_at
                """,
                (
                    source["id"],
                    source["name"],
                    source["publisher"],
                    source["homepage_url"],
                    source["source_type"],
                    source["category"],
                    source["source_tier"],
                    enabled,
                    payload,
                    normalize_trust_grade(source.get("trust_grade", source.get("source_tier", "A")), default="A"),
                    int(bool(source.get("batch_candidate_allowed", False))),
                    str(source.get("discovery_policy") or "official_only"),
                    now,
                    now,
                ),
            )

    def list_sources(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM sources"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY source_tier, name"
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._source_row(row) for row in rows]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        return self._source_row(row) if row else None

    def ensure_source_tasks(
        self,
        sources: list[dict[str, Any]],
        *,
        initial_delay_seconds: int = 0,
    ) -> None:
        """Register enabled sources without resetting their durable queue state."""
        now = datetime.now(timezone.utc)
        first_attempt = (now + timedelta(seconds=max(0, initial_delay_seconds))).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            for source in sources:
                priority = int(source.get("config", {}).get("queue_priority", 100))
                connection.execute(
                    """
                    INSERT INTO source_tasks (
                        source_id, priority, status, attempts, next_attempt_at, updated_at
                    ) VALUES (?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        priority=excluded.priority,
                        updated_at=excluded.updated_at
                    """,
                    (source["id"], priority, first_attempt, utc_now()),
                )

    def get_source_task(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_tasks WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_source_tasks(self, *, due_only: bool = False) -> list[dict[str, Any]]:
        # Keep disabled tasks visible for audit, but never expose them through
        # the executable (due-only) queue.  The source registry is the durable
        # authority for whether a collector may run.
        query = "SELECT source_tasks.* FROM source_tasks"
        values: list[Any] = []
        if due_only:
            query += (
                " JOIN sources ON sources.id = source_tasks.source_id"
                " WHERE sources.enabled = 1"
                " AND source_tasks.next_attempt_at <= ?"
                " AND (source_tasks.lease_until IS NULL OR source_tasks.lease_until < ?)"
            )
            now = utc_now()
            values.extend((now, now))
        query += " ORDER BY source_tasks.priority ASC, source_tasks.next_attempt_at ASC, source_tasks.source_id ASC"
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def claim_source_task(
        self,
        source_id: str,
        *,
        lease_seconds: int = 1_800,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically claim a task so a second worker cannot duplicate it.

        Scheduled workers claim only due tasks.  An operator-triggered
        recovery may set ``force=True`` to run a healthy source immediately,
        while still respecting an active lease held by another worker.
        """
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now = now_dt.isoformat().replace("+00:00", "Z")
        lease = (now_dt + timedelta(seconds=max(1, lease_seconds))).isoformat().replace(
            "+00:00", "Z"
        )
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM source_tasks
                JOIN sources ON sources.id = source_tasks.source_id
                WHERE source_tasks.source_id = ?
                  AND sources.enabled = 1
                  AND (? OR source_tasks.next_attempt_at <= ?)
                  AND (source_tasks.lease_until IS NULL OR source_tasks.lease_until < ?)
                  AND source_tasks.status IN ('pending', 'failed', 'blocked', 'succeeded')
                """,
                (source_id, force, now, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE source_tasks
                SET status='running', attempts=attempts + 1,
                    lease_until=?, last_started_at=?, last_run_id=NULL,
                    last_error=NULL, last_error_class=NULL, updated_at=?
                WHERE source_id=?
                """,
                (lease, now, now, source_id),
            )
            claimed = connection.execute(
                "SELECT * FROM source_tasks WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(claimed) if claimed else None

    def complete_source_task(
        self,
        source_id: str,
        *,
        next_attempt_seconds: int,
        run_id: int | None = None,
    ) -> None:
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        next_attempt = (
            now_dt + timedelta(seconds=max(0, next_attempt_seconds))
        ).isoformat().replace("+00:00", "Z")
        now = now_dt.isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE source_tasks
                SET status='succeeded', next_attempt_at=?, lease_until=NULL,
                    last_finished_at=?, last_run_id=COALESCE(?, last_run_id),
                    last_error=NULL, last_error_class=NULL,
                    consecutive_failures=0, updated_at=?
                WHERE source_id=?
                """,
                (next_attempt, now, run_id, now, source_id),
            )

    def fail_source_task(
        self,
        source_id: str,
        *,
        error: str,
        error_class: str,
        retry_after_seconds: int,
        blocked: bool = False,
        run_id: int | None = None,
    ) -> None:
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        next_attempt = (
            now_dt + timedelta(seconds=max(1, retry_after_seconds))
        ).isoformat().replace("+00:00", "Z")
        now = now_dt.isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE source_tasks
                SET status=?, next_attempt_at=?, lease_until=NULL,
                    last_finished_at=?, last_run_id=COALESCE(?, last_run_id),
                    last_error=?, last_error_class=?,
                    consecutive_failures=consecutive_failures + 1,
                    updated_at=?
                WHERE source_id=?
                """,
                (
                    "blocked" if blocked else "failed",
                    next_attempt,
                    now,
                    run_id,
                    str(error)[:1500],
                    str(error_class)[:120],
                    now,
                    source_id,
                ),
            )

    def recover_expired_source_tasks(self) -> list[dict[str, Any]]:
        """Return expired task leases to the durable pending queue.

        ``crawl_runs`` and ``source_tasks`` represent separate failure modes.
        Closing an abandoned crawl row alone is not sufficient: a task left in
        ``running`` is not eligible for another claim, even after its lease has
        expired.  Recovering the lease preserves the task's attempt history and
        records an operator-visible reason, while allowing the next scheduled
        sync to make a fresh, independently audited attempt.
        """

        now = utc_now()
        message = "Source task lease expired before completion; returned to pending queue."
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_tasks
                WHERE status = 'running'
                  AND lease_until IS NOT NULL
                  AND lease_until < ?
                ORDER BY lease_until ASC, source_id ASC
                """,
                (now,),
            ).fetchall()
            connection.executemany(
                """
                UPDATE source_tasks
                SET status = 'pending', next_attempt_at = ?, lease_until = NULL,
                    last_finished_at = ?, last_error = ?,
                    last_error_class = 'lease_expired', updated_at = ?
                WHERE source_id = ? AND status = 'running' AND lease_until < ?
                """,
                [
                    (now, now, message, now, str(row["source_id"]), now)
                    for row in rows
                ],
            )
        return [dict(row) for row in rows]

    def set_source_enabled(self, source_id: str, enabled: bool) -> None:
        """Set one source's durable collection state after an audited decision."""

        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sources SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), utc_now(), source_id),
            )
            if not cursor.rowcount:
                raise ValueError(f"Source is not registered: {source_id}")
            if enabled:
                connection.execute(
                    """UPDATE jobs SET source_availability_status = 'available',
                        source_unavailable_reason = '',
                        publication_layer = COALESCE(NULLIF(publication_layer_before_unavailable, ''), publication_layer),
                        publication_layer_before_unavailable = NULL
                       WHERE source_id = ? AND publication_layer = 'source_unavailable'""",
                    (source_id,),
                )
            else:
                connection.execute(
                    """UPDATE jobs SET source_availability_status = 'source_unavailable',
                        source_unavailable_reason = 'source_disabled',
                        publication_layer_before_unavailable = CASE
                          WHEN publication_layer <> 'source_unavailable' THEN publication_layer
                          ELSE publication_layer_before_unavailable END,
                        publication_layer = 'source_unavailable'
                       WHERE source_id = ? AND status = 'open'
                         AND last_official_verified_at IS NOT NULL
                         AND publication_layer IN ('exact_match', 'broad_geoscience_match',
                                                   'incomplete_but_basic_match', 'source_unavailable')""",
                    (source_id,),
                )

    def record_source_health(
        self,
        source_id: str,
        *,
        status: str,
        detail: str = "",
        status_code: int | None = None,
        successful: bool = False,
    ) -> None:
        """Store public-entry availability; scan outcomes live in ``crawl_runs``."""
        now = utc_now()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT last_success_at FROM source_health WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            last_success_at = (
                now
                if successful
                else (existing["last_success_at"] if existing else None)
            )
            connection.execute(
                """
                INSERT INTO source_health (
                    source_id, status, checked_at, last_success_at, status_code, detail
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    status=excluded.status,
                    checked_at=excluded.checked_at,
                    last_success_at=excluded.last_success_at,
                    status_code=excluded.status_code,
                    detail=excluded.detail
                """,
                (
                    source_id,
                    status,
                    now,
                    last_success_at,
                    status_code,
                    detail[:1000],
                ),
            )
            healthy = bool(successful) or status in {"ok", "healthy", "source_available"}
            if healthy:
                connection.execute(
                    """UPDATE jobs
                       SET source_availability_status = 'available',
                           source_unavailable_reason = '',
                           publication_layer = COALESCE(
                               NULLIF(publication_layer_before_unavailable, ''),
                               publication_layer
                           ),
                           publication_layer_before_unavailable = NULL
                     WHERE source_id = ? AND publication_layer = 'source_unavailable'""",
                    (source_id,),
                )
            else:
                phase_state = "degraded" if status in {"source_degraded", "degraded"} else "source_unavailable"
                reason = "source_disabled" if status in {"disabled", "source_disabled"} else "unknown"
                connection.execute(
                    """UPDATE jobs
                       SET source_availability_status = ?,
                           source_unavailable_reason = ?,
                           publication_layer_before_unavailable = CASE
                               WHEN publication_layer <> 'source_unavailable'
                               THEN publication_layer
                               ELSE publication_layer_before_unavailable END,
                           publication_layer = 'source_unavailable'
                     WHERE source_id = ?
                       AND status = 'open'
                       AND last_official_verified_at IS NOT NULL
                       AND publication_layer IN ('exact_match', 'broad_geoscience_match',
                                                 'incomplete_but_basic_match', 'source_unavailable')""",
                    (phase_state, reason, source_id),
                )

    def get_source_health(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_health WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def record_source_capture_freshness(
        self,
        source_id: str,
        *,
        captured_at: str,
        crawl_run_id: int | None = None,
        evidence_kind: str = "capture_manifest",
    ) -> None:
        """Persist a proven complete-capture timestamp for public reads.

        A successful HTTP request or source sync is not enough to prove a
        snapshot is current. Callers may write this projection only after a
        complete capture has supplied its own evidence timestamp.
        """

        raw_timestamp = str(captured_at or "").strip()
        try:
            parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("captured_at must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        normalized_timestamp = (
            parsed.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_capture_freshness (
                    source_id, captured_at, recorded_at, crawl_run_id, evidence_kind
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    captured_at=excluded.captured_at,
                    recorded_at=excluded.recorded_at,
                    crawl_run_id=excluded.crawl_run_id,
                    evidence_kind=excluded.evidence_kind
                """,
                (
                    source_id,
                    normalized_timestamp,
                    utc_now(),
                    crawl_run_id,
                    str(evidence_kind or "capture_manifest")[:100],
                ),
            )

    def get_source_capture_freshness(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_capture_freshness WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return dict(row) if row else None

    def clear_source_capture_freshness(self, source_id: str) -> None:
        """Remove an invalidated capture projection after a lifecycle change.

        This only removes the read-side freshness projection; source rows and
        their immutable crawl/audit history remain available for inspection.
        """

        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM source_capture_freshness WHERE source_id = ?",
                (source_id,),
            )

    def list_source_health(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_health ORDER BY checked_at DESC, source_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_government_source_verification(
        self,
        source_id: str,
        *,
        status: str,
        checked_at: str,
        detail: str = "",
        evidence_fingerprint: str = "",
        availability_confirmed_at: str | None = None,
        availability_confirmation_detail: str = "",
    ) -> dict[str, Any]:
        """Persist official evidence and optional current-vacancy confirmation.

        ``availability_confirmed_at`` is intentionally distinct from a normal
        URL recheck. It is written after either an operator confirmation or an
        explicitly configured official current-status page matches its open
        marker. Ordinary automated refreshes preserve, but never renew, that
        assertion.
        """
        if status not in {"verified", "source_unavailable", "withdrawn", "not_configured"}:
            raise ValueError(f"Unsupported government source verification status: {status}")
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT last_success_at, availability_confirmed_at,
                       availability_confirmation_detail
                FROM government_source_verifications WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
            last_success_at = (
                checked_at
                if status == "verified"
                else (existing["last_success_at"] if existing else None)
            )
            persisted_availability_confirmed_at = (
                availability_confirmed_at
                if availability_confirmed_at is not None
                else (existing["availability_confirmed_at"] if existing else None)
            )
            persisted_availability_detail = (
                availability_confirmation_detail
                if availability_confirmed_at is not None
                else (existing["availability_confirmation_detail"] if existing else "")
            )
            connection.execute(
                """
                INSERT INTO government_source_verifications (
                    source_id, status, checked_at, last_success_at,
                    availability_confirmed_at, availability_confirmation_detail,
                    detail, evidence_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    status=excluded.status,
                    checked_at=excluded.checked_at,
                    last_success_at=excluded.last_success_at,
                    availability_confirmed_at=excluded.availability_confirmed_at,
                    availability_confirmation_detail=excluded.availability_confirmation_detail,
                    detail=excluded.detail,
                    evidence_fingerprint=excluded.evidence_fingerprint
                """,
                (
                    source_id,
                    status,
                    checked_at,
                    last_success_at,
                    persisted_availability_confirmed_at,
                    persisted_availability_detail[:2000],
                    detail[:2000],
                    evidence_fingerprint[:128],
                ),
            )
            connection.execute(
                """
                INSERT INTO government_source_verification_events (
                    source_id, status, checked_at, last_success_at, detail,
                    evidence_fingerprint, availability_confirmed_at,
                    availability_confirmation_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    status,
                    checked_at,
                    last_success_at,
                    detail[:2000],
                    evidence_fingerprint[:128],
                    persisted_availability_confirmed_at,
                    persisted_availability_detail[:2000],
                ),
            )
            row = connection.execute(
                "SELECT * FROM government_source_verifications WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by the insert above
            raise RuntimeError("Government verification could not be persisted")
        return dict(row)

    def list_government_source_verifications(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM government_source_verifications ORDER BY checked_at DESC, source_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_coverage_snapshot(
        self,
        snapshot_date: str,
        report: dict[str, Any],
    ) -> None:
        """Persist one compact, replaceable daily quality observation.

        This is deliberately separate from immutable daily reports.  It records
        whether the source network and the 100-person explicit-match result are
        improving from day to day, even when no employment bulletin is published.
        """
        try:
            date.fromisoformat(snapshot_date)
        except (TypeError, ValueError) as error:
            raise ValueError("snapshot_date must use ISO YYYY-MM-DD format") from error

        profile_summary = report.get("profile_match_quality", {}).get(
            "cohort_summary", {}
        )
        scan_quality = report.get("scan_quality", {})
        distribution = report.get("job_distribution", {})
        compact_payload = {
            "field_completeness": report.get("field_completeness", {}),
            "location_quality": report.get("location_quality", {}),
            "deadline_quality": report.get("deadline_quality", {}),
            "source_target_totals": report.get("source_target_matrix", {}).get(
                "totals", {}
            ),
            "quality_gate": report.get("quality_gate", {}),
            "scorecard": report.get("scorecard", {}),
        }
        values = (
            snapshot_date,
            utc_now(),
            int(report.get("open_jobs", 0)),
            int(profile_summary.get("explicit_job_profile_matches", 0)),
            int(profile_summary.get("review_job_profile_matches", 0)),
            int(profile_summary.get("students_with_explicit_match", 0)),
            int(scan_quality.get("successful_scan_sources", 0)),
            int(scan_quality.get("sources_with_open_matches", 0)),
            float(distribution.get("source_concentration", {}).get("top_share", 0)),
            float(distribution.get("category_concentration", {}).get("top_share", 0)),
            json.dumps(compact_payload, ensure_ascii=False),
        )
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO coverage_snapshots (
                    snapshot_date, captured_at, open_jobs,
                    explicit_job_profile_matches, review_job_profile_matches,
                    students_with_explicit_match, successful_scan_sources,
                    sources_with_open_matches, source_top_share,
                    category_top_share, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    captured_at=excluded.captured_at,
                    open_jobs=excluded.open_jobs,
                    explicit_job_profile_matches=excluded.explicit_job_profile_matches,
                    review_job_profile_matches=excluded.review_job_profile_matches,
                    students_with_explicit_match=excluded.students_with_explicit_match,
                    successful_scan_sources=excluded.successful_scan_sources,
                    sources_with_open_matches=excluded.sources_with_open_matches,
                    source_top_share=excluded.source_top_share,
                    category_top_share=excluded.category_top_share,
                    payload_json=excluded.payload_json
                """,
                values,
            )

    def list_coverage_snapshots(self, limit: int = 2) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM coverage_snapshots
                ORDER BY snapshot_date DESC, captured_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        snapshots = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            snapshots.append(item)
        return snapshots

    def record_crawl_start(
        self,
        source_id: str,
        *,
        transport_mode: str = "environment",
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crawl_runs (
                    source_id, started_at, status, outcome, transport_mode
                )
                VALUES (?, ?, 'running', 'running', ?)
                """,
                (source_id, utc_now(), str(transport_mode or "environment")),
            )
            return int(cursor.lastrowid)

    def record_crawl_finish(
        self,
        run_id: int,
        status: str,
        discovered_count: int = 0,
        inserted_count: int = 0,
        updated_count: int = 0,
        error_message: str | None = None,
        *,
        open_matching_count: int = 0,
        outcome: str | None = None,
        attempts: int = 0,
        retryable_failures: int = 0,
        transport_mode: str | None = None,
        evidence_complete_count: int = 0,
        manual_review_count: int = 0,
        attachment_success_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE crawl_runs
                SET finished_at = ?, status = ?, discovered_count = ?,
                    open_matching_count = ?, inserted_count = ?,
                    updated_count = ?, error_message = ?, outcome = ?,
                    attempts = ?, retryable_failures = ?,
                    transport_mode = COALESCE(?, transport_mode),
                    evidence_complete_count = ?, manual_review_count = ?,
                    attachment_success_count = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    utc_now(),
                    status,
                    discovered_count,
                    open_matching_count,
                    inserted_count,
                    updated_count,
                    error_message,
                    str(outcome or "unknown"),
                    max(0, int(attempts)),
                    max(0, int(retryable_failures)),
                    transport_mode,
                    max(0, int(evidence_complete_count)),
                    max(0, int(manual_review_count)),
                    max(0, int(attachment_success_count)),
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )

    def update_crawl_run_metrics(
        self,
        run_id: int,
        *,
        attachment_success_count: int | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        """Add post-collection metrics without reopening a completed run."""

        with self.transaction() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM crawl_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Crawl run does not exist: {run_id}")
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update(metadata_updates or {})
            connection.execute(
                """
                UPDATE crawl_runs
                SET attachment_success_count = COALESCE(?, attachment_success_count),
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    None
                    if attachment_success_count is None
                    else max(0, int(attachment_success_count)),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )

    def stale_crawl_runs(
        self,
        max_age_seconds: int,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return unfinished runs that are older than the operational threshold.

        A process can be terminated between recording a crawl start and recording
        its completion. This query deliberately does not change those records so
        a pre-publication audit can still block an unsafe publish if necessary.
        """
        cutoff = self._crawl_run_cutoff(max_age_seconds)
        clauses = ["crawl_runs.status = 'running'", "crawl_runs.started_at < ?"]
        values: list[Any] = [cutoff]
        if source_id:
            clauses.append("crawl_runs.source_id = ?")
            values.append(source_id)
        where = " AND ".join(clauses)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT crawl_runs.*, sources.name AS source_name
                FROM crawl_runs
                LEFT JOIN sources ON sources.id = crawl_runs.source_id
                WHERE {where}
                ORDER BY crawl_runs.started_at ASC
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_stale_crawl_runs(
        self,
        max_age_seconds: int,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Close only demonstrably abandoned crawl records without deleting data."""
        cutoff = self._crawl_run_cutoff(max_age_seconds)
        clauses = ["crawl_runs.status = 'running'", "crawl_runs.started_at < ?"]
        values: list[Any] = [cutoff]
        if source_id:
            clauses.append("crawl_runs.source_id = ?")
            values.append(source_id)
        where = " AND ".join(clauses)
        finished_at = utc_now()
        message = (
            "Crawl run was marked interrupted after exceeding the configured "
            f"{max_age_seconds}-second timeout."
        )
        with self.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT crawl_runs.*, sources.name AS source_name
                FROM crawl_runs
                LEFT JOIN sources ON sources.id = crawl_runs.source_id
                WHERE {where}
                ORDER BY crawl_runs.started_at ASC
                """,
                values,
            ).fetchall()
            connection.executemany(
                """
                UPDATE crawl_runs
                SET status = 'interrupted', finished_at = ?, error_message = ?
                WHERE id = ? AND status = 'running'
                """,
                [(finished_at, message, int(row["id"])) for row in rows],
            )
        return [dict(row) for row in rows]

    def mark_source_synced(self, source_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE sources SET last_synced_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), source_id),
            )

    @staticmethod
    def _phase3_basic_major_evidence(job: dict[str, Any]) -> bool:
        evidence = job.get("field_evidence") or {}
        if not isinstance(evidence, dict):
            return False
        values: list[str] = []
        for key, value in evidence.items():
            if key == "fields" and isinstance(value, dict):
                values.extend(str(item or "") for item in value.values())
            elif str(key).casefold() in {
                "专业范围", "专业要求", "专业", "major", "qualification", "岗位要求"
            }:
                values.append(str(value or ""))
        text = " ".join(values)
        return any(
            term in text
            for term in (
                "地质学", "地质工程", "地质资源与地质工程", "资源勘查工程",
                "地质类", "地质学类", "地质工程类", "地质资源与地质工程类",
                "地学类", "工学大类", "专业不限", "不限专业",
            )
        )

    def _update_phase3_fields(
        self,
        connection: sqlite3.Connection,
        job_id: int,
        job: dict[str, Any],
        *,
        publication_status: str,
        publication_basis: dict[str, Any],
        now: str,
    ) -> None:
        """Persist the independent Phase 3 contract for one job row."""

        source_grade = str(job.get("trust_grade") or job.get("source_tier") or "A")
        trust_grade = normalize_trust_grade(source_grade, default="A")
        availability = str(
            job.get("source_availability_status") or "available"
        ).strip()
        if availability not in SOURCE_AVAILABILITY_STATES:
            availability = "available"
        reason = str(job.get("source_unavailable_reason") or "").strip()[:500]
        layer = normalize_publication_layer(job.get("publication_layer"))
        if layer == "not_public":
            basic_major_evidence = self._phase3_basic_major_evidence(job)
            # A forged legacy publication marker is deliberately downgraded by
            # the write gate. Its explanatory basis is not enough to promote
            # the row into the new incomplete-but-basic public layer.
            if "未通过岗位级专业匹配复核" in str(publication_basis.get("reason") or ""):
                basic_major_evidence = False
            layer = derive_publication_layer(
                publication_status,
                publication_basis,
                has_basic_major_evidence=basic_major_evidence,
            )
        source_row = connection.execute(
            "SELECT enabled FROM sources WHERE id = ?",
            (str(job.get("source_id") or ""),),
        ).fetchone()
        source_disabled = source_row is not None and not bool(source_row["enabled"])
        last_verified = str(
            job.get("last_official_verified_at")
            or (now if str(job.get("verification_status") or "").startswith("published") else "")
        ).strip() or None
        expiry = str(job.get("no_deadline_expires_at") or "").strip() or None
        if not str(job.get("deadline_date") or "").strip() and not expiry:
            expiry = no_deadline_expiry(job.get("published_date"), last_verified)
        if source_disabled and last_verified and layer != "not_public":
            layer = "source_unavailable"
            availability = "source_unavailable"
            reason = reason or "source_disabled"
        withdrawal_reason = str(job.get("withdrawal_reason") or "").strip()
        if withdrawal_reason and withdrawal_reason not in WITHDRAWAL_REASONS:
            withdrawal_reason = ""
        connection.execute(
            """
            UPDATE jobs SET
                trust_grade = ?, source_availability_status = ?,
                source_unavailable_reason = ?, publication_layer = ?,
                publication_layer_before_unavailable = ?,
                third_party_table_url = ?, official_table_url = ?,
                official_notice_url = ?, official_application_url = ?,
                table_sha256 = ?, table_sheet_name = ?, table_row_number = ?,
                position_code = ?, withdrawal_reason = ?,
                withdrawal_evidence_url = ?, last_official_verified_at = ?,
                no_deadline_expires_at = ?
            WHERE id = ?
            """,
            (
                trust_grade,
                availability,
                reason,
                layer,
                job.get("publication_layer_before_unavailable"),
                job.get("third_party_table_url"),
                job.get("official_table_url"),
                job.get("official_notice_url", job.get("official_evidence_url")),
                job.get("official_application_url", job.get("application_url")),
                job.get("table_sha256"),
                job.get("table_sheet_name"),
                job.get("table_row_number"),
                job.get("position_code"),
                withdrawal_reason or None,
                job.get("withdrawal_evidence_url"),
                last_verified,
                expiry,
                job_id,
            ),
        )

    def save_job(self, job: dict[str, Any]) -> tuple[int, str]:
        now = utc_now()
        # Legacy adapters and imports can bypass the primary matcher. Recheck
        # the job-level evidence here; a supplied match marker is not proof.
        publication_status = str(job.get("publication_status", "pending_evidence"))
        publication_basis = job.get("publication_basis", {})
        if not isinstance(publication_basis, dict):
            publication_basis = {}
        publication_basis = dict(publication_basis)
        public_broad_categories = {
            "公务员与选调",
            "事业单位与人才引进",
        }
        government_type = str(
            (job.get("field_evidence") or {}).get("政府岗位类型") or ""
        ).strip()
        is_public_broad = (
            str(job.get("category") or "") in public_broad_categories
            or government_type in {"public_institution", "civil_service"}
        )
        if (
            publication_status in PUBLIC_STUDENT_PUBLICATION_STATUSES
            and publication_status != "source_unavailable"
            and not is_public_broad
        ):
            decision = evaluate_student_publication(job)
            if (
                decision.status != publication_status
                or decision.major_match_mode
                not in {"exact_four_names", "similar_major", "public_unrestricted_rule"}
                or publication_basis.get("major_match_mode") != decision.major_match_mode
            ):
                publication_status = "pending_evidence"
                publication_basis.update(
                    {
                        "status": "pending_evidence",
                        "label": "待补岗位级证据",
                        "reason": (
                            "非公务员/事业编岗位未通过岗位级专业匹配复核："
                            f"{decision.reason}"
                        ),
                        "major_match_mode": "",
                        "matched_profile_ids": [],
                    }
                )
            else:
                publication_basis["matched_profile_ids"] = list(decision.matched_profile_ids)
        requested_status = str(job.get("status", "open") or "open").strip()
        requested_withdrawal_reason = str(job.get("withdrawal_reason") or "").strip()
        persisted_status = requested_status
        if requested_status == "withdrawn" and requested_withdrawal_reason not in WITHDRAWAL_REASONS:
            # A status flag alone is not withdrawal evidence.  This prevents
            # legacy collectors from reintroducing freshness/missing-scan
            # withdrawals while preserving explicit audited closures.
            persisted_status = "open"
        raw_precedence = job.get("official_detail_precedence", 100)
        try:
            detail_precedence = int(raw_precedence)
        except (TypeError, ValueError):
            detail_precedence = 100
        if isinstance(raw_precedence, bool) or not 0 <= detail_precedence <= 10_000:
            detail_precedence = 100
        json_fields = {
            "provinces_json": json.dumps(
                job.get("provinces", []), ensure_ascii=False
            ),
            "degree_levels_json": json.dumps(
                job.get("degree_levels", []), ensure_ascii=False
            ),
            "major_tags_json": json.dumps(job.get("major_tags", []), ensure_ascii=False),
            "field_evidence_json": json.dumps(
                job.get("field_evidence", {}), ensure_ascii=False
            ),
            "publication_basis_json": json.dumps(
                publication_basis, ensure_ascii=False
            ),
        }
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id, content_hash FROM jobs WHERE fingerprint = ?",
                (job["fingerprint"],),
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO jobs (
                        source_id, external_id, fingerprint, content_hash, title,
                        employer, group_name, category, source_tier, source_name,
                        source_url, application_url, location,
                        canonical_employer_id, canonical_employer_name,
                        parent_employer_name, province, provinces_json, city, country_or_region,
                        location_confidence, location_evidence, verification_status,
                        official_evidence_url, published_date,
                        official_detail_key, official_detail_precedence,
                         deadline_date, degree_levels_json, major_tags_json,
                         field_evidence_json, publication_status, publication_basis_json,
                         summary,
                        description, relevance_score, relevance_band, status,
                        first_seen_at, last_seen_at, created_at, updated_at
                    )
                     VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?
                     )
                    """,
                    (
                        job.get("source_id"),
                        job.get("external_id"),
                        job["fingerprint"],
                        job["content_hash"],
                        job["title"],
                        job["employer"],
                        job.get("group_name"),
                        job["category"],
                        job["source_tier"],
                        job["source_name"],
                        job["source_url"],
                        job.get("application_url"),
                        job.get("location"),
                        job.get("canonical_employer_id"),
                        job.get("canonical_employer_name"),
                        job.get("parent_employer_name"),
                        job.get("province"),
                        json_fields["provinces_json"],
                        job.get("city"),
                        job.get("country_or_region"),
                        job.get("location_confidence", "unknown"),
                        job.get("location_evidence"),
                        job.get("verification_status", "published_official"),
                        job.get("official_evidence_url", job["source_url"]),
                        job.get("published_date"),
                        job.get("official_detail_key"),
                        detail_precedence,
                        job.get("deadline_date"),
                        json_fields["degree_levels_json"],
                        json_fields["major_tags_json"],
                        json_fields["field_evidence_json"],
                        publication_status,
                        json_fields["publication_basis_json"],
                        job.get("summary", ""),
                        job.get("description", ""),
                        job["relevance_score"],
                        job["relevance_band"],
                        persisted_status,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                job_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                    VALUES (?, 'created', ?, ?)
                    """,
                    (job_id, now, json.dumps({"title": job["title"]}, ensure_ascii=False)),
                )
                self._ensure_official_page_evidence(connection, job_id, job)
                self._update_phase3_fields(
                    connection, job_id, job,
                    publication_status=publication_status,
                    publication_basis=publication_basis,
                    now=now,
                )
                return job_id, "created"

            job_id = int(existing["id"])
            if existing["content_hash"] == job["content_hash"]:
                connection.execute(
                    """
                    UPDATE jobs
                    SET external_id = ?, title = ?, employer = ?, group_name = ?,
                        category = ?, source_tier = ?, source_name = ?,
                        source_url = ?, application_url = ?, location = ?,
                        canonical_employer_id = ?, canonical_employer_name = ?,
                        parent_employer_name = ?, province = ?, provinces_json = ?, city = ?,
                        country_or_region = ?, location_confidence = ?,
                        location_evidence = ?, verification_status = ?,
                        official_evidence_url = ?,
                        published_date = ?, official_detail_key = ?,
                        official_detail_precedence = ?, deadline_date = ?, degree_levels_json = ?,
                         major_tags_json = ?, field_evidence_json = ?,
                         publication_status = ?, publication_basis_json = ?,
                         summary = ?, description = ?,
                        relevance_score = ?, relevance_band = ?, status = ?,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        job.get("external_id"),
                        job["title"],
                        job["employer"],
                        job.get("group_name"),
                        job["category"],
                        job["source_tier"],
                        job["source_name"],
                        job["source_url"],
                        job.get("application_url"),
                        job.get("location"),
                        job.get("canonical_employer_id"),
                        job.get("canonical_employer_name"),
                        job.get("parent_employer_name"),
                        job.get("province"),
                        json_fields["provinces_json"],
                        job.get("city"),
                        job.get("country_or_region"),
                        job.get("location_confidence", "unknown"),
                        job.get("location_evidence"),
                        job.get("verification_status", "published_official"),
                        job.get("official_evidence_url", job["source_url"]),
                        job.get("published_date"),
                        job.get("official_detail_key"),
                        detail_precedence,
                        job.get("deadline_date"),
                        json_fields["degree_levels_json"],
                        json_fields["major_tags_json"],
                        json_fields["field_evidence_json"],
                        publication_status,
                        json_fields["publication_basis_json"],
                        job.get("summary", ""),
                        job.get("description", ""),
                        job["relevance_score"],
                        job["relevance_band"],
                        persisted_status,
                        now,
                        job_id,
                    ),
                )
                self._ensure_official_page_evidence(connection, job_id, job)
                self._update_phase3_fields(
                    connection, job_id, job,
                    publication_status=publication_status,
                    publication_basis=publication_basis,
                    now=now,
                )
                return job_id, "unchanged"

            connection.execute(
                """
                UPDATE jobs
                SET external_id = ?, content_hash = ?, title = ?, employer = ?,
                    group_name = ?, category = ?, source_tier = ?, source_name = ?,
                    source_url = ?, application_url = ?, location = ?,
                    canonical_employer_id = ?, canonical_employer_name = ?,
                    parent_employer_name = ?, province = ?, provinces_json = ?, city = ?,
                    country_or_region = ?, location_confidence = ?,
                    location_evidence = ?, verification_status = ?,
                    official_evidence_url = ?,
                    published_date = ?, official_detail_key = ?,
                    official_detail_precedence = ?, deadline_date = ?, degree_levels_json = ?,
                     major_tags_json = ?, field_evidence_json = ?,
                     publication_status = ?, publication_basis_json = ?,
                     summary = ?, description = ?,
                    relevance_score = ?, relevance_band = ?, status = ?,
                    last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    job.get("external_id"),
                    job["content_hash"],
                    job["title"],
                    job["employer"],
                    job.get("group_name"),
                    job["category"],
                    job["source_tier"],
                    job["source_name"],
                    job["source_url"],
                    job.get("application_url"),
                    job.get("location"),
                    job.get("canonical_employer_id"),
                    job.get("canonical_employer_name"),
                    job.get("parent_employer_name"),
                    job.get("province"),
                    json_fields["provinces_json"],
                    job.get("city"),
                    job.get("country_or_region"),
                    job.get("location_confidence", "unknown"),
                    job.get("location_evidence"),
                    job.get("verification_status", "published_official"),
                    job.get("official_evidence_url", job["source_url"]),
                    job.get("published_date"),
                    job.get("official_detail_key"),
                    detail_precedence,
                    job.get("deadline_date"),
                    json_fields["degree_levels_json"],
                        json_fields["major_tags_json"],
                        json_fields["field_evidence_json"],
                    publication_status,
                        json_fields["publication_basis_json"],
                        job.get("summary", ""),
                    job.get("description", ""),
                    job["relevance_score"],
                    job["relevance_band"],
                    persisted_status,
                    now,
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                VALUES (?, 'updated', ?, ?)
                """,
                (job_id, now, json.dumps({"title": job["title"]}, ensure_ascii=False)),
            )
            self._ensure_official_page_evidence(connection, job_id, job)
            self._update_phase3_fields(
                connection, job_id, job,
                publication_status=publication_status,
                publication_basis=publication_basis,
                now=now,
            )
            return job_id, "updated"

    def supersede_duplicate_jobs(self, source_id: str) -> int:
        """Hide same-source title duplicates while preserving their history.

        Some official portals expose one announcement through several public
        routes (for example an announcement page and a position-page mirror).
        The lower-priority record is retained for audit history but marked
        ``superseded`` so the student-facing open count is not inflated.
        """
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, title, source_url, updated_at
                FROM jobs
                WHERE source_id = ? AND status = 'open'
                ORDER BY updated_at DESC, id DESC
                """,
                (source_id,),
            ).fetchall()
            selected: dict[str, sqlite3.Row] = {}
            duplicate_ids: list[tuple[int, int]] = []

            def key(value: str) -> str:
                return re.sub(r"\s+", "", str(value or "")).casefold()

            def rank(url: str) -> int:
                parsed = urlparse(str(url or ""))
                if "/campus/view/" in parsed.path:
                    return 0
                if "/news/view/" in parsed.path:
                    return 1
                if "/job/view/" in parsed.path:
                    return 2
                return 3

            for row in rows:
                title_key = key(row["title"])
                previous = selected.get(title_key)
                if previous is None:
                    selected[title_key] = row
                    continue
                if rank(row["source_url"]) < rank(previous["source_url"]):
                    duplicate_ids.append((int(previous["id"]), int(row["id"])))
                    selected[title_key] = row
                else:
                    duplicate_ids.append((int(row["id"]), int(previous["id"])))

            now = utc_now()
            for duplicate_id, winner_id in duplicate_ids:
                connection.execute(
                    "UPDATE jobs SET status = 'superseded', superseded_successor_job_id = ?, "
                    "superseded_reason = ?, superseded_at = ?, updated_at = ? WHERE id = ?",
                    (winner_id, "duplicate_same_source", now, now, duplicate_id),
                )
                connection.execute(
                    """
                    INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                    VALUES (?, 'superseded', ?, ?)
                    """,
                    (
                        duplicate_id,
                        now,
                        json.dumps(
                            {"source_id": source_id, "winner_job_id": winner_id},
                            ensure_ascii=False,
                        ),
                    ),
                )
            return len(duplicate_ids)

    def upsert_source_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        """Register one attachment whose parent page and file URL are official."""
        normalized = validate_source_artifact(artifact)
        now = utc_now()
        with self.transaction() as connection:
            source = connection.execute(
                "SELECT * FROM sources WHERE id = ?", (normalized["source_id"],)
            ).fetchone()
            if source is None:
                raise ValueError("Source artifact references an unregistered source_id")
            self._validate_source_artifact_urls(
                self._source_row(source), normalized
            )
            existing_artifact = connection.execute(
                """
                SELECT extraction_status, metadata_json
                FROM source_artifacts
                WHERE source_id = ? AND artifact_url = ?
                """,
                (normalized["source_id"], normalized["artifact_url"]),
            ).fetchone()
            if existing_artifact is not None:
                try:
                    existing_metadata = json.loads(existing_artifact["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    existing_metadata = {}
                if isinstance(existing_metadata, dict):
                    merged_metadata = dict(existing_metadata)
                    merged_metadata.update(normalized["metadata"])
                    normalized["metadata"] = merged_metadata
            connection.execute(
                """
                INSERT INTO source_artifacts (
                    source_id, parent_url, artifact_url, artifact_kind, media_type,
                    content_sha256, storage_path, parser_version, extraction_status,
                    metadata_json, discovered_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, artifact_url) DO UPDATE SET
                    parent_url=excluded.parent_url,
                    artifact_kind=excluded.artifact_kind,
                    media_type=COALESCE(excluded.media_type, source_artifacts.media_type),
                    content_sha256=COALESCE(
                        excluded.content_sha256, source_artifacts.content_sha256
                    ),
                    storage_path=COALESCE(excluded.storage_path, source_artifacts.storage_path),
                    parser_version=COALESCE(
                        excluded.parser_version, source_artifacts.parser_version
                    ),
                    -- A recurring manifest refresh must not reset an artifact
                    -- that has already been downloaded, extracted, or failed.
                    -- Re-registration may advance metadata, but only an
                    -- explicit processor call may change processing state.
                    extraction_status=CASE
                        WHEN source_artifacts.extraction_status <> 'registered'
                             AND excluded.extraction_status = 'registered'
                        THEN source_artifacts.extraction_status
                        ELSE excluded.extraction_status
                    END,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    normalized["source_id"],
                    normalized["parent_url"],
                    normalized["artifact_url"],
                    normalized["artifact_kind"],
                    normalized["media_type"],
                    normalized["content_sha256"],
                    normalized["storage_path"],
                    normalized["parser_version"],
                    normalized["extraction_status"],
                    json.dumps(normalized["metadata"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM source_artifacts
                WHERE source_id = ? AND artifact_url = ?
                """,
                (normalized["source_id"], normalized["artifact_url"]),
            ).fetchone()
        if row is None:  # pragma: no cover - INSERT/SELECT is atomic in this transaction
            raise RuntimeError("Source artifact could not be persisted")
        return self._artifact_row(row)

    def get_source_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return self._artifact_row(row) if row else None

    def list_source_artifacts(
        self,
        source_id: str | None = None,
        *,
        limit: int = 100,
        oldest_first: bool = False,
        extraction_statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM source_artifacts"
        values: list[Any] = []
        predicates: list[str] = []
        if source_id:
            predicates.append("source_id = ?")
            values.append(source_id)
        if extraction_statuses:
            statuses = sorted({str(item).strip() for item in extraction_statuses if str(item).strip()})
            if statuses:
                predicates.append(
                    "extraction_status IN (" + ", ".join("?" for _ in statuses) + ")"
                )
                values.extend(statuses)
        if predicates:
            query += " WHERE " + " AND ".join(predicates)
        query += (
            " ORDER BY updated_at ASC, id ASC LIMIT ?"
            if oldest_first
            else " ORDER BY updated_at DESC, id DESC LIMIT ?"
        )
        values.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._artifact_row(row) for row in rows]

    def iter_source_artifacts(
        self,
        *,
        source_id: str | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Read the complete artifact ledger for internal processing."""
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        last_id = 0
        while True:
            conditions = ["id > ?"]
            values: list[Any] = [last_id]
            if source_id:
                conditions.append("source_id = ?")
                values.append(source_id)
            values.append(batch_size)
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM source_artifacts WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY id ASC LIMIT ?",
                    values,
                ).fetchall()
            if not rows:
                return
            for row in rows:
                last_id = int(row["id"])
                yield self._artifact_row(row)

    def list_source_artifact_status_counts(self) -> list[dict[str, Any]]:
        """Summarize controlled attachment extraction by official source."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id,
                       SUM(CASE WHEN extraction_status = 'extracted' THEN 1 ELSE 0 END)
                           AS extracted_count,
                       SUM(CASE WHEN extraction_status IN ('failed', 'skipped') THEN 1 ELSE 0 END)
                           AS failed_count,
                       COUNT(*) AS total_count
                FROM source_artifacts
                GROUP BY source_id
                ORDER BY source_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_government_source_verification_events(
        self,
        source_id: str | None = None,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return immutable official-evidence refresh events newest first."""
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        with self.connect() as connection:
            if source_id:
                rows = connection.execute(
                    """
                    SELECT * FROM government_source_verification_events
                    WHERE source_id = ?
                    ORDER BY checked_at DESC, id DESC
                    LIMIT ?
                    """,
                    (source_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM government_source_verification_events
                    ORDER BY checked_at DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def government_source_refresh_counts(self) -> dict[str, dict[str, int]]:
        """Summarize successful and total evidence refresh events per source."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id,
                       COUNT(*) AS total_refreshes,
                       SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END)
                           AS successful_refreshes
                FROM government_source_verification_events
                GROUP BY source_id
                ORDER BY source_id
                """
            ).fetchall()
        return {
            str(row["source_id"]): {
                "total_refreshes": int(row["total_refreshes"] or 0),
                "successful_refreshes": int(row["successful_refreshes"] or 0),
            }
            for row in rows
        }

    def update_source_artifact_processing(
        self,
        artifact_id: int,
        *,
        extraction_status: str,
        media_type: str | None = None,
        content_sha256: str | None = None,
        storage_path: str | None = None,
        parser_version: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a controlled download or parser result without changing its URL."""
        if metadata_updates is not None and not isinstance(metadata_updates, dict):
            raise ValueError("metadata_updates must be an object")
        now = utc_now()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM source_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            if existing is None:
                raise ValueError("Source artifact does not exist")
            source = connection.execute(
                "SELECT * FROM sources WHERE id = ?", (existing["source_id"],)
            ).fetchone()
            if source is None:  # pragma: no cover - protected by the foreign key
                raise ValueError("Source artifact references an unregistered source_id")
            metadata = json.loads(existing["metadata_json"])
            metadata.update(metadata_updates or {})
            payload = {
                "source_id": existing["source_id"],
                "parent_url": existing["parent_url"],
                "artifact_url": existing["artifact_url"],
                "artifact_kind": existing["artifact_kind"],
                "media_type": media_type if media_type is not None else existing["media_type"],
                "content_sha256": (
                    content_sha256
                    if content_sha256 is not None
                    else existing["content_sha256"]
                ),
                "storage_path": (
                    storage_path if storage_path is not None else existing["storage_path"]
                ),
                "parser_version": (
                    parser_version
                    if parser_version is not None
                    else existing["parser_version"]
                ),
                "extraction_status": extraction_status,
                "metadata": metadata,
            }
            normalized = validate_source_artifact(payload)
            self._validate_source_artifact_urls(
                self._source_row(source), normalized
            )
            connection.execute(
                """
                UPDATE source_artifacts
                SET media_type = ?, content_sha256 = ?, storage_path = ?,
                    parser_version = ?, extraction_status = ?, metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized["media_type"],
                    normalized["content_sha256"],
                    normalized["storage_path"],
                    normalized["parser_version"],
                    normalized["extraction_status"],
                    json.dumps(normalized["metadata"], ensure_ascii=False),
                    now,
                    artifact_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM source_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - protected by the UPDATE above
            raise RuntimeError("Source artifact processing state could not be persisted")
        return self._artifact_row(row)

    def upsert_source_artifact_rows(
        self,
        artifact_id: int,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist extracted table rows with stable identities for later review."""
        normalized_rows = [self._normalize_artifact_row(item) for item in rows]
        if not normalized_rows:
            return []
        now = utc_now()
        persisted: list[dict[str, Any]] = []
        with self.transaction() as connection:
            artifact = connection.execute(
                "SELECT id FROM source_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            if artifact is None:
                raise ValueError("Source artifact does not exist")
            for item in normalized_rows:
                row_key = self._artifact_row_key(artifact_id, item)
                # Older parser versions hashed a missing row_key_suffix as
                # JSON null, while current rows normalize it to an empty
                # string.  Match by the stable table coordinates as well so
                # reparsing an existing attachment cannot create a second
                # identity for the same official row.
                coordinate_rows = connection.execute(
                    """
                    SELECT * FROM source_artifact_rows
                    WHERE artifact_id = ? AND sheet_name = ?
                      AND row_number = ? AND row_kind = ?
                    ORDER BY id ASC
                    """,
                    (
                        artifact_id,
                        item["sheet_name"],
                        item["row_number"],
                        item["row_kind"],
                    ),
                ).fetchall()
                exact = next(
                    (candidate for candidate in coordinate_rows if candidate["row_key"] == row_key),
                    None,
                )
                if exact is not None:
                    target = exact
                elif not str(item.get("row_key_suffix") or "").strip():
                    target = self._choose_artifact_row_identity(
                        connection,
                        coordinate_rows,
                        canonical_key=row_key,
                    )
                else:
                    target = None
                if target is None:
                    connection.execute(
                        """
                        INSERT INTO source_artifact_rows (
                            artifact_id, row_key, sheet_name, row_number, row_kind,
                            cells_json, row_text, extraction_confidence, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            artifact_id,
                            row_key,
                            item["sheet_name"],
                            item["row_number"],
                            item["row_kind"],
                            json.dumps(item["cells"], ensure_ascii=False, sort_keys=True),
                            item["row_text"],
                            item["extraction_confidence"],
                            now,
                            now,
                        ),
                    )
                    target = connection.execute(
                        """
                        SELECT * FROM source_artifact_rows
                        WHERE artifact_id = ? AND row_key = ?
                        """,
                        (artifact_id, row_key),
                    ).fetchone()
                else:
                    if target["row_key"] != row_key:
                        collision = connection.execute(
                            """
                            SELECT id FROM source_artifact_rows
                            WHERE artifact_id = ? AND row_key = ? AND id != ?
                            """,
                            (artifact_id, row_key, target["id"]),
                        ).fetchone()
                        if collision is not None:
                            # The target keeps the best candidate identity;
                            # move the lower-priority legacy row out of the
                            # unique-key slot before claiming the canonical
                            # key. It remains available as historical evidence.
                            connection.execute(
                                "UPDATE source_artifact_rows SET row_key = ? WHERE id = ?",
                                (
                                    f"legacy-superseded:{artifact_id}:{collision['id']}:{collision['id']}",
                                    collision["id"],
                                ),
                            )
                        connection.execute(
                            "UPDATE source_artifact_rows SET row_key = ? WHERE id = ?",
                            (row_key, target["id"]),
                        )
                    connection.execute(
                        """
                        UPDATE source_artifact_rows
                        SET cells_json = ?, row_text = ?, extraction_confidence = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(item["cells"], ensure_ascii=False, sort_keys=True),
                            item["row_text"],
                            item["extraction_confidence"],
                            now,
                            target["id"],
                        ),
                    )
                    self._retire_duplicate_artifact_rows(
                        connection,
                        coordinate_rows,
                        target_row_id=int(target["id"]),
                    )
                row = connection.execute(
                    """
                    SELECT * FROM source_artifact_rows
                    WHERE artifact_id = ? AND row_key = ?
                    """,
                    (artifact_id, row_key),
                ).fetchone()
                if row is None:  # pragma: no cover - protected by UPSERT above
                    raise RuntimeError("Source artifact row could not be persisted")
                persisted.append(self._source_artifact_row(row))
        return persisted

    @staticmethod
    def _artifact_candidate_status_rank(status: Any) -> int:
        return {
            "published": 5,
            "official_content_verified": 4,
            "needs_review": 3,
            "rejected": 1,
            "expired": 0,
        }.get(str(status or ""), 0)

    @classmethod
    def _choose_artifact_row_identity(
        cls,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
        *,
        canonical_key: str,
    ) -> sqlite3.Row | None:
        if not rows:
            return None
        candidate_rows = connection.execute(
            """
            SELECT artifact_row_id, review_status
            FROM artifact_job_candidates
            WHERE artifact_row_id IN (%s)
            """ % ",".join("?" for _ in rows),
            [row["id"] for row in rows],
        ).fetchall()
        statuses = {
            int(row["artifact_row_id"]): str(row["review_status"] or "")
            for row in candidate_rows
        }
        return max(
            rows,
            key=lambda row: (
                cls._artifact_candidate_status_rank(statuses.get(int(row["id"]), "")),
                int(row["row_key"] == canonical_key),
                int(row["id"]),
            ),
        )

    @classmethod
    def _retire_duplicate_artifact_rows(
        cls,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
        *,
        target_row_id: int,
    ) -> None:
        """Retire legacy coordinate duplicates without losing a verified row.

        A duplicate candidate is retained as an explicit rejected audit record;
        it can no longer compete during verification. Rows without candidates
        are safe to remove because the current target row stores the complete
        attachment evidence and raw file retention remains independent.
        """
        target_candidate = connection.execute(
            """
            SELECT id, review_status FROM artifact_job_candidates
            WHERE artifact_row_id = ?
            """,
            (target_row_id,),
        ).fetchone()
        for row in rows:
            duplicate_id = int(row["id"])
            if duplicate_id == target_row_id:
                continue
            duplicate = connection.execute(
                """
                SELECT id, review_status FROM artifact_job_candidates
                WHERE artifact_row_id = ?
                """,
                (duplicate_id,),
            ).fetchone()
            if duplicate is None:
                connection.execute(
                    "DELETE FROM source_artifact_rows WHERE id = ?",
                    (duplicate_id,),
                )
                continue
            if target_candidate is None:
                candidate_key = cls._artifact_candidate_key(target_row_id)
                connection.execute(
                    """
                    UPDATE artifact_job_candidates
                    SET artifact_row_id = ?, candidate_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (target_row_id, candidate_key, utc_now(), duplicate["id"]),
                )
                target_candidate = duplicate
                continue
            if cls._artifact_candidate_status_rank(duplicate["review_status"]) > cls._artifact_candidate_status_rank(target_candidate["review_status"]):
                # The target was selected before this call, so this branch is
                # defensive for rows introduced by a concurrent legacy run.
                connection.execute(
                    "UPDATE artifact_job_candidates SET review_status = ?, review_note = ?, updated_at = ? WHERE id = ?",
                    (
                        "rejected",
                        "附件行身份规范化：保留另一条同坐标候选作为主记录；本条仅作历史证据。",
                        utc_now(),
                        target_candidate["id"],
                    ),
                )
            connection.execute(
                """
                UPDATE artifact_job_candidates
                SET review_status = ?,
                    review_note = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    "rejected",
                    "附件行身份规范化：同一官方表格坐标已由当前解析行替代；本条仅作历史证据。",
                    utc_now(),
                    duplicate["id"],
                ),
            )

    def list_source_artifact_rows(
        self,
        artifact_id: int,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_artifact_rows
                WHERE artifact_id = ?
                ORDER BY sheet_name ASC, row_number ASC, id ASC
                LIMIT ?
                """,
                (artifact_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._source_artifact_row(row) for row in rows]

    def iter_source_artifact_rows(
        self,
        artifact_id: int,
        *,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Read every extracted row without the operator API's 500-row cap."""
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        last_id = 0
        while True:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM source_artifact_rows
                    WHERE artifact_id = ? AND id > ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (artifact_id, last_id, batch_size),
                ).fetchall()
            if not rows:
                return
            for row in rows:
                last_id = int(row["id"])
                yield self._source_artifact_row(row)

    def upsert_artifact_job_candidate(
        self,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a private, review-required job candidate from one table row."""
        normalized = validate_artifact_candidate(candidate)
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT rows.artifact_id, artifacts.source_id, artifacts.parent_url
                FROM source_artifact_rows AS rows
                JOIN source_artifacts AS artifacts ON artifacts.id = rows.artifact_id
                WHERE rows.id = ?
                """,
                (normalized["artifact_row_id"],),
            ).fetchone()
            if row is None:
                raise ValueError("Artifact job candidate references an unknown artifact row")
            if str(row["source_id"]) != normalized["source_id"]:
                raise ValueError("Artifact job candidate source must match its attachment")
            if str(row["parent_url"]) != normalized["official_page_url"]:
                raise ValueError(
                    "Artifact job candidate official_page_url must be its attachment parent_url"
                )
            candidate_key = self._artifact_candidate_key(normalized["artifact_row_id"])
            existing = connection.execute(
                """
                SELECT id, review_status FROM artifact_job_candidates
                WHERE artifact_row_id = ?
                """,
                (normalized["artifact_row_id"],),
            ).fetchone()
            if existing is not None and existing["review_status"] == "published":
                persisted = self._select_artifact_job_candidate(
                    connection, int(existing["id"])
                )
                if persisted is None:  # pragma: no cover - protected by SELECT above
                    raise RuntimeError("Published artifact candidate disappeared")
                return self._artifact_candidate_row(persisted)
            connection.execute(
                """
                INSERT INTO artifact_job_candidates (
                    artifact_row_id, source_id, candidate_key, official_page_url,
                    title, employer, application_url, location, published_date,
                    deadline_date, degree_levels_json, major_tags_json, summary,
                    description, field_evidence_json, relevance_score, review_status,
                    review_note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_row_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    candidate_key = excluded.candidate_key,
                    official_page_url = excluded.official_page_url,
                    title = excluded.title,
                    employer = excluded.employer,
                    application_url = excluded.application_url,
                    location = excluded.location,
                    published_date = excluded.published_date,
                    deadline_date = excluded.deadline_date,
                    degree_levels_json = excluded.degree_levels_json,
                    major_tags_json = excluded.major_tags_json,
                    summary = excluded.summary,
                    description = excluded.description,
                    field_evidence_json = excluded.field_evidence_json,
                    relevance_score = excluded.relevance_score,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized["artifact_row_id"],
                    normalized["source_id"],
                    candidate_key,
                    normalized["official_page_url"],
                    normalized["title"],
                    normalized["employer"],
                    normalized["application_url"],
                    normalized["location"],
                    normalized["published_date"],
                    normalized["deadline_date"],
                    json.dumps(normalized["degree_levels"], ensure_ascii=False),
                    json.dumps(normalized["major_tags"], ensure_ascii=False),
                    normalized["summary"] or "",
                    normalized["description"],
                    json.dumps(normalized["field_evidence"], ensure_ascii=False),
                    normalized["relevance_score"],
                    normalized["review_status"],
                    normalized["review_note"] or "",
                    now,
                    now,
                ),
            )
            candidate_row = connection.execute(
                "SELECT id FROM artifact_job_candidates WHERE artifact_row_id = ?",
                (normalized["artifact_row_id"],),
            ).fetchone()
            if candidate_row is None:  # pragma: no cover - protected by UPSERT
                raise RuntimeError("Artifact job candidate could not be persisted")
            persisted = self._select_artifact_job_candidate(
                connection, int(candidate_row["id"])
            )
        if persisted is None:  # pragma: no cover - protected by SELECT above
            raise RuntimeError("Artifact job candidate could not be loaded")
        return self._artifact_candidate_row(persisted)

    def get_artifact_job_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = self._select_artifact_job_candidate(connection, candidate_id)
        return self._artifact_candidate_row(row) if row else None

    def list_artifact_job_candidates(
        self,
        *,
        artifact_id: int | None = None,
        review_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = self._artifact_candidate_select_sql()
        values: list[Any] = []
        conditions: list[str] = []
        if artifact_id is not None:
            conditions.append("rows.artifact_id = ?")
            values.append(artifact_id)
        if review_status:
            conditions.append("candidates.review_status = ?")
            values.append(review_status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY candidates.updated_at DESC, candidates.id DESC LIMIT ?"
        values.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._artifact_candidate_row(row) for row in rows]

    def iter_artifact_job_candidates(
        self,
        *,
        artifact_id: int | None = None,
        review_status: str | None = None,
        batch_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """Read the complete private candidate ledger in stable ID order."""
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        last_id = 0
        while True:
            conditions = ["candidates.id > ?"]
            values: list[Any] = [last_id]
            if artifact_id is not None:
                conditions.append("rows.artifact_id = ?")
                values.append(artifact_id)
            if review_status:
                conditions.append("candidates.review_status = ?")
                values.append(review_status)
            query = (
                self._artifact_candidate_select_sql()
                + " WHERE " + " AND ".join(conditions)
                + " ORDER BY candidates.id ASC LIMIT ?"
            )
            values.append(batch_size)
            with self.connect() as connection:
                rows = connection.execute(query, values).fetchall()
            if not rows:
                return
            for row in rows:
                last_id = int(row["id"])
                yield self._artifact_candidate_row(row)

    def expire_stale_artifact_candidates(self, *, as_of: str) -> int:
        """Move unreviewed attachment rows past their deadline out of review.

        The source file and extracted evidence remain immutable.  Only private
        candidates that have not been published are transitioned, so an old
        table cannot keep inflating the operator queue or be mistaken for a
        current opportunity after a daily sync.
        """
        try:
            date.fromisoformat(as_of)
        except ValueError as error:
            raise ValueError("as_of must use YYYY-MM-DD") from error
        note = f"系统自动过期：报名截止日期早于 {as_of}。原始附件证据已保留。"
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE artifact_job_candidates
                SET review_status = 'expired',
                    review_note = CASE
                        WHEN review_note IS NULL OR review_note = '' THEN ?
                        WHEN review_note LIKE '%' || ? || '%' THEN review_note
                        ELSE review_note || '；' || ?
                    END,
                    updated_at = ?
                WHERE review_status IN ('needs_review', 'official_content_verified')
                  AND deadline_date IS NOT NULL
                  AND deadline_date <> ''
                  AND deadline_date < ?
                """,
                (note, note, note, now, as_of),
            )
            return int(cursor.rowcount)

    def reject_unreviewed_artifact_candidates(
        self,
        artifact_id: int,
        *,
        reason: str,
        keep_artifact_row_ids: list[int] | None = None,
    ) -> int:
        """Remove parser-invalid rows from the private review queue.

        This preserves every raw attachment and row.  Only unreviewed rows are
        moved to ``rejected`` so a real prior human verification or publication
        is never silently overwritten by an evolving parser rule.
        """
        note = self._optional_text(reason)
        if not note:
            raise ValueError("Artifact candidate rejection requires a reason")
        retained = sorted({int(value) for value in keep_artifact_row_ids or []})
        query = """
            UPDATE artifact_job_candidates
            SET review_status = 'rejected',
                review_note = CASE
                    WHEN review_note IS NULL OR review_note = '' THEN ?
                    WHEN review_note LIKE '%' || ? || '%' THEN review_note
                    ELSE review_note || '；' || ?
                END,
                updated_at = ?
            WHERE review_status = 'needs_review'
              AND artifact_row_id IN (
                  SELECT id FROM source_artifact_rows WHERE artifact_id = ?
              )
        """
        values: list[Any] = [note, note, note, utc_now(), artifact_id]
        if retained:
            placeholders = ", ".join("?" for _ in retained)
            query += f" AND artifact_row_id NOT IN ({placeholders})"
            values.extend(retained)
        with self.transaction() as connection:
            cursor = connection.execute(query, values)
            return int(cursor.rowcount)

    def update_artifact_job_candidate(
        self,
        candidate_id: int,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Advance private attachment candidates only after a human review note."""
        if not isinstance(changes, dict):
            raise ValueError("Artifact candidate changes must be an object")
        current = self.get_artifact_job_candidate(candidate_id)
        if current is None:
            return None
        # Official tables often omit location while the notice body identifies
        # the institution address. Location may be filled only in the private
        # review queue and is required before public publication.
        editable = {
            "review_status",
            "review_note",
            "title",
            "employer",
            "application_url",
            "location",
            "published_date",
            "deadline_date",
            "degree_levels",
            "major_tags",
            "summary",
            "description",
            "location_evidence",
            "field_evidence_updates",
        }
        unexpected = set(changes) - editable
        if unexpected:
            raise ValueError(
                "Artifact candidate fields are not editable after extraction: "
                + ", ".join(sorted(unexpected))
            )
        if current["review_status"] == "published" and set(changes) - {
            "review_status", "review_note", "field_evidence_updates"
        }:
            raise ValueError("Published artifact rows allow evidence refresh only")
        review_note = self._optional_text(
            changes.get("review_note", current.get("review_note"))
        ) or ""
        review_status = validate_artifact_candidate_transition(
            current["review_status"],
            changes.get("review_status", current["review_status"]),
            review_note=review_note,
        )
        if review_status == "published" and current["review_status"] != "published":
            raise ValueError(
                "Use mark_artifact_job_candidate_published after saving the verified job"
            )
        text_fields = (
            "title",
            "employer",
            "application_url",
            "location",
            "published_date",
            "deadline_date",
            "summary",
            "description",
        )
        values = {
            field: self._optional_text(changes.get(field, current.get(field)))
            for field in text_fields
        }
        if not values["title"] or not values["employer"]:
            raise ValueError("Verified attachment candidates require title and employer")
        if values["application_url"] and not is_http_url(values["application_url"]):
            raise ValueError("application_url must be an HTTP(S) URL")
        if "degree_levels" in changes:
            if not isinstance(changes["degree_levels"], list):
                raise ValueError("degree_levels must be a list")
            degree_levels = [self._optional_text(item) for item in changes["degree_levels"]]
            degree_levels = [item for item in degree_levels if item]
        else:
            degree_levels = list(current.get("degree_levels") or [])
        if "major_tags" in changes:
            if not isinstance(changes["major_tags"], list):
                raise ValueError("major_tags must be a list")
            major_tags = [self._optional_text(item) for item in changes["major_tags"]]
            major_tags = [item for item in major_tags if item]
        else:
            major_tags = list(current.get("major_tags") or [])
        field_evidence = current.get("field_evidence")
        if not isinstance(field_evidence, dict):
            field_evidence = {}
        else:
            field_evidence = dict(field_evidence)
        location_evidence = self._optional_text(
            changes.get("location_evidence")
        )
        if location_evidence:
            field_evidence["工作地点依据"] = location_evidence
        evidence_updates = changes.get("field_evidence_updates")
        if evidence_updates is not None:
            if not isinstance(evidence_updates, dict):
                raise ValueError("field_evidence_updates must be an object")
            for key, value in evidence_updates.items():
                key_text = str(key).strip()
                value_text = self._optional_text(value)
                if not key_text:
                    raise ValueError("field_evidence_updates keys cannot be empty")
                if value_text is None:
                    field_evidence.pop(key_text, None)
                else:
                    field_evidence[key_text] = value_text
        field_evidence["岗位"] = values["title"]
        if "major_tags" in changes and major_tags and "专业范围" not in (evidence_updates or {}):
            field_evidence["专业范围"] = "、".join(major_tags)
        if "degree_levels" in changes and degree_levels and "学历要求" not in (evidence_updates or {}):
            field_evidence["学历要求"] = "、".join(degree_levels)
        if values["location"]:
            field_evidence["工作地点"] = values["location"]
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE artifact_job_candidates
                SET review_status = ?, review_note = ?, title = ?, employer = ?,
                    application_url = ?, location = ?, published_date = ?,
                    deadline_date = ?, degree_levels_json = ?, major_tags_json = ?,
                    summary = ?, description = ?, field_evidence_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    review_status,
                    review_note,
                    values["title"],
                    values["employer"],
                    values["application_url"],
                    values["location"],
                    values["published_date"],
                    values["deadline_date"],
                    json.dumps(degree_levels, ensure_ascii=False),
                    json.dumps(major_tags, ensure_ascii=False),
                    values["summary"] or "",
                    values["description"] or values["title"],
                    json.dumps(field_evidence, ensure_ascii=False),
                    now,
                    candidate_id,
                ),
            )
            row = self._select_artifact_job_candidate(connection, candidate_id)
        return self._artifact_candidate_row(row) if row else None

    def mark_artifact_job_candidate_published(
        self,
        candidate_id: int,
        job_id: int,
    ) -> dict[str, Any]:
        """Seal the private candidate after its verified job and evidence exist."""
        now = utc_now()
        with self.transaction() as connection:
            candidate = self._select_artifact_job_candidate(connection, candidate_id)
            if candidate is None:
                raise ValueError("Artifact job candidate does not exist")
            if candidate["review_status"] != "official_content_verified":
                raise ValueError("Artifact job candidate has not passed official-content review")
            artifact = connection.execute(
                """
                SELECT extraction_status, content_sha256
                FROM source_artifacts
                WHERE id = ?
                """,
                (candidate["artifact_id"],),
            ).fetchone()
            if artifact is None or artifact["extraction_status"] != "extracted":
                raise ValueError("Artifact must finish extraction before publication")
            if not artifact["content_sha256"]:
                raise ValueError("Artifact publication requires a content hash")
            job = connection.execute(
                "SELECT source_id, source_url FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise ValueError("Published job does not exist")
            if str(job["source_id"] or "") != str(candidate["source_id"]):
                raise ValueError("Published job source must match the artifact candidate")
            if str(job["source_url"] or "") != str(candidate["official_page_url"]):
                raise ValueError("Published job source_url must be the official parent page")
            connection.execute(
                """
                UPDATE artifact_job_candidates
                SET review_status = 'published', published_job_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (job_id, now, candidate_id),
            )
            row = self._select_artifact_job_candidate(connection, candidate_id)
        if row is None:  # pragma: no cover - protected by UPDATE above
            raise RuntimeError("Artifact candidate publication state could not be persisted")
        return self._artifact_candidate_row(row)

    def publish_artifact_job_candidate(
        self,
        candidate_id: int,
        job: dict[str, Any],
        attachment_evidence: dict[str, Any],
    ) -> tuple[int, str, dict[str, Any]]:
        """Publish an attachment candidate and all of its evidence atomically.

        ``save_job``, ``add_job_evidence`` and the candidate state transition
        each use ``transaction()``.  The re-entrant transaction context keeps
        those calls on this one connection, so any validation or database
        error rolls back the public job, evidence and private state together.
        """
        with self.transaction() as connection:
            candidate_row = self._select_artifact_job_candidate(
                connection, candidate_id
            )
            if candidate_row is None:
                raise ValueError("Artifact job candidate does not exist")
            candidate = self._artifact_candidate_row(candidate_row)
            if candidate["review_status"] != "official_content_verified":
                raise ValueError(
                    "Artifact job candidate has not passed official-content review"
                )
            if candidate.get("extraction_confidence") == "low":
                raise ValueError(
                    "Low-confidence OCR candidates require manual import after independent verification"
                )
            evidence_artifact_id = attachment_evidence.get("artifact_id")
            if int(evidence_artifact_id or 0) != int(candidate["artifact_id"]):
                raise ValueError(
                    "Attachment evidence must reference the candidate's artifact"
                )
            if str(attachment_evidence.get("evidence_url") or "") != str(
                candidate["artifact_url"]
            ):
                raise ValueError(
                    "Attachment evidence must reference the candidate's artifact URL"
                )

            job_id, outcome = self.save_job(job)
            self.add_job_evidence(job_id, attachment_evidence)
            published = self.mark_artifact_job_candidate_published(
                candidate_id, job_id
            )
            return job_id, outcome, published

    def withdraw_published_artifact_job(
        self,
        candidate_id: int,
        *,
        reason: str,
        evidence_url: str | None = None,
    ) -> bool:
        """Withdraw an attachment row only with explicit official proof.

        A reason string alone is not evidence.  The caller must bind the
        allowed lifecycle reason to an auditable official URL; download
        failures, hash drift and a missing row therefore remain review states.
        """

        normalized_reason = str(reason or "").strip().lower()
        allowed_reason = normalized_reason if normalized_reason in WITHDRAWAL_REASONS else None
        if allowed_reason is None or not is_http_url(evidence_url):
            # Download failure, hash drift, a missing row, and parser changes
            # are review states, never withdrawal evidence.
            return False
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT jobs.id, jobs.title, jobs.external_id, jobs.source_url
                FROM artifact_job_candidates AS candidates
                JOIN jobs ON jobs.id = candidates.published_job_id
                WHERE candidates.id = ? AND candidates.review_status = 'published'
                  AND jobs.source_id = candidates.source_id
                  AND jobs.source_url = candidates.official_page_url
                  AND jobs.status = 'open'
                """,
                (candidate_id,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """UPDATE jobs SET status = 'withdrawn', withdrawal_reason = ?,
                   withdrawal_evidence_url = ?, updated_at = ? WHERE id = ?""",
                (allowed_reason, str(evidence_url).strip(), now, int(row["id"])),
            )
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                VALUES (?, 'withdrawn', ?, ?)
                """,
                (
                    int(row["id"]),
                    now,
                    json.dumps(
                        {
                            "title": row["title"],
                            "external_id": row["external_id"],
                            "reason": reason,
                            "artifact_candidate_id": candidate_id,
                            "artifact_evidence_lapsed": True,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            return True

    def refresh_published_artifact_job(
        self, candidate_id: int, job: dict[str, Any]
    ) -> str:
        """Refresh a verified row, reopening only our own evidence withdrawal."""
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT candidates.published_job_id, candidates.source_id,
                       candidates.official_page_url, jobs.status, jobs.external_id
                FROM artifact_job_candidates AS candidates
                JOIN jobs ON jobs.id = candidates.published_job_id
                WHERE candidates.id = ? AND candidates.review_status = 'published'
                """,
                (candidate_id,),
            ).fetchone()
            if row is None:
                return "skipped"
            expected_external_id = f"artifact-candidate-{candidate_id}"
            if (
                str(row["source_id"]) != str(job.get("source_id") or "")
                or str(row["official_page_url"]) != str(job.get("source_url") or "")
                or str(row["external_id"]) != expected_external_id
                or str(job.get("external_id") or "") != expected_external_id
                or str(job.get("status") or "open") != "open"
            ):
                return "skipped"
            previous_status = str(row["status"])
            if previous_status == "withdrawn":
                withdrawal = connection.execute(
                    """
                    SELECT payload_json FROM job_events
                    WHERE job_id = ? AND event_type = 'withdrawn'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(row["published_job_id"]),),
                ).fetchone()
                payload = json.loads(withdrawal["payload_json"]) if withdrawal else {}
                if (
                    not payload.get("artifact_evidence_lapsed")
                    or payload.get("artifact_candidate_id") != candidate_id
                ):
                    return "skipped"
            elif previous_status != "open":
                return "skipped"
            saved_id, _ = self.save_job(job)
            if saved_id != int(row["published_job_id"]):
                raise ValueError("Published artifact refresh changed the linked job identity")
            return "restored" if previous_status == "withdrawn" else "refreshed"

    def add_job_evidence(
        self,
        job_id: int,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach a validated public evidence record to one exact job."""
        normalized = validate_job_evidence(evidence)
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT id, source_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise ValueError("Job evidence references an unknown job_id")
            artifact_id = normalized["artifact_id"]
            if artifact_id is not None:
                artifact = connection.execute(
                    "SELECT source_id, artifact_url FROM source_artifacts WHERE id = ?",
                    (artifact_id,),
                ).fetchone()
                if artifact is None:
                    raise ValueError("Job evidence references an unknown artifact_id")
                if str(job["source_id"] or "") != str(artifact["source_id"]):
                    raise ValueError(
                        "Job evidence artifact must belong to the job's registered source"
                    )
                if (
                    normalized["evidence_type"] == "attachment"
                    and normalized["evidence_url"] != str(artifact["artifact_url"])
                ):
                    raise ValueError(
                        "Attachment evidence_url must match the registered artifact URL"
                    )
            return self._upsert_job_evidence(connection, job_id, normalized)

    def list_job_evidence(
        self,
        job_id: int,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_evidence
                WHERE job_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (job_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._evidence_row(row) for row in rows]

    def has_verified_official_evidence(self, job_id: int) -> bool:
        placeholders = ", ".join("?" for _ in OFFICIAL_EVIDENCE_TYPES)
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT 1
                FROM job_evidence
                WHERE job_id = ?
                  AND verification_status = 'verified'
                  AND evidence_type IN ({placeholders})
                LIMIT 1
                """,
                (job_id, *sorted(OFFICIAL_EVIDENCE_TYPES)),
            ).fetchone()
        return row is not None

    def verified_official_evidence_job_ids(self) -> set[int]:
        """Return the audit-ready evidence set in one query for large job pools."""
        placeholders = ", ".join("?" for _ in OFFICIAL_EVIDENCE_TYPES)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT job_id
                FROM job_evidence
                WHERE verification_status = 'verified'
                  AND evidence_type IN ({placeholders})
                """,
                tuple(sorted(OFFICIAL_EVIDENCE_TYPES)),
            ).fetchall()
        return {int(row["job_id"]) for row in rows}

    def _ensure_official_page_evidence(
        self,
        connection: sqlite3.Connection,
        job_id: int,
        job: dict[str, Any],
    ) -> None:
        official_url = str(
            job.get("official_evidence_url") or job.get("source_url") or ""
        ).strip()
        if not is_http_url(official_url):
            return
        self._upsert_job_evidence(
            connection,
            job_id,
            {
                "evidence_type": "official_page",
                "field_name": "job_record",
                "evidence_url": official_url,
                "locator": "official_evidence_url",
                "verification_status": "verified",
                "metadata": {"origin": "job_save"},
            },
        )

    @staticmethod
    def _evidence_key(job_id: int, evidence: dict[str, Any]) -> str:
        identity = {
            "job_id": job_id,
            "artifact_id": evidence.get("artifact_id"),
            "evidence_type": evidence["evidence_type"],
            "field_name": evidence["field_name"],
            "evidence_url": evidence["evidence_url"],
            "locator": evidence.get("locator"),
        }
        serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _upsert_job_evidence(
        connection: sqlite3.Connection,
        job_id: int,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = validate_job_evidence(evidence)
        evidence_key = normalized.get("evidence_key") or Database._evidence_key(
            job_id, normalized
        )
        existing = connection.execute(
            "SELECT job_id FROM job_evidence WHERE evidence_key = ?", (evidence_key,)
        ).fetchone()
        if existing is not None and int(existing["job_id"]) != job_id:
            raise ValueError("evidence_key is already assigned to another job")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO job_evidence (
                job_id, artifact_id, evidence_key, evidence_type, field_name,
                evidence_url, locator, excerpt, verification_status, metadata_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_key) DO UPDATE SET
                artifact_id=excluded.artifact_id,
                evidence_type=excluded.evidence_type,
                field_name=excluded.field_name,
                evidence_url=excluded.evidence_url,
                locator=excluded.locator,
                excerpt=excluded.excerpt,
                verification_status=excluded.verification_status,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                job_id,
                normalized["artifact_id"],
                evidence_key,
                normalized["evidence_type"],
                normalized["field_name"],
                normalized["evidence_url"],
                normalized["locator"],
                normalized["excerpt"],
                normalized["verification_status"],
                json.dumps(normalized["metadata"], ensure_ascii=False),
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM job_evidence WHERE evidence_key = ?", (evidence_key,)
        ).fetchone()
        if row is None:  # pragma: no cover - protected by the preceding INSERT
            raise RuntimeError("Job evidence could not be persisted")
        return Database._evidence_row(row)

    @staticmethod
    def _public_freshness_filter(as_of_date: str) -> tuple[str, list[str]]:
        """Return the explicit deadline/90-day undated lifecycle predicate."""
        date.fromisoformat(as_of_date)
        predicate = (
            "(jobs.deadline_date >= ? OR "
            "(jobs.deadline_date IS NULL OR jobs.deadline_date = '') AND "
            "(jobs.no_deadline_expires_at IS NULL OR jobs.no_deadline_expires_at = '' "
            "OR jobs.no_deadline_expires_at >= ?))"
        )
        return predicate, [as_of_date, as_of_date]

    @staticmethod
    def _source_capture_freshness_filter(
        as_of_date: str | None,
    ) -> tuple[str, list[str]]:
        """Return a neutral predicate: freshness is evidence health only.

        The function remains as a compatibility seam for callers and old
        reports.  Phase 3 explicitly forbids a 30-hour capture window from
        changing student visibility or the job lifecycle.
        """

        if as_of_date is not None:
            date.fromisoformat(as_of_date)
        return "1 = 1", []

    def _student_visible_jobs_cte(
        self,
        *,
        as_of_date: str | None = None,
    ) -> tuple[str, list[Any]]:
        """Build the one canonical public row per official detail page.

        The underlying records are intentionally retained one-per-source for
        evidence, source reconciliation and fallback.  This read-side rank
        removes only URL-proven duplicates from student-facing results.  A
        source with lower ``official_detail_precedence`` wins; if it becomes
        disabled, stale or withdrawn it leaves the candidate set and another
        live observation takes over automatically.
        """

        clauses = [
            (
                "jobs.status IN ('open', 'expired')"
                if as_of_date is not None
                else "jobs.status = 'open'"
            ),
            # ``superseded`` is a historical lineage state, never a public
            # withdrawal and never a source-health decision.  Keep this
            # explicit even though the status predicate above excludes it so
            # future historical-read modes cannot accidentally expose it.
            "jobs.status <> 'superseded'",
            "(jobs.publication_status IN ('student_eligible', 'unrestricted_eligible', 'source_unavailable') "
            "OR jobs.publication_layer IN ('exact_match', 'broad_geoscience_match', "
            "'incomplete_but_basic_match', 'source_unavailable'))",
            "jobs.country_or_region IN ('中国大陆', '中国')",
            "(jobs.publication_layer <> 'source_unavailable' OR jobs.last_official_verified_at IS NOT NULL)",
        ]
        values: list[Any] = []
        if as_of_date is not None:
            freshness_sql, freshness_values = self._public_freshness_filter(
                as_of_date
            )
            clauses.append(freshness_sql)
            values.extend(freshness_values)
        else:
            clauses.append(
                "(jobs.deadline_date >= date('now') OR "
                "(jobs.deadline_date IS NULL OR jobs.deadline_date = '') AND "
                "(jobs.no_deadline_expires_at IS NULL OR jobs.no_deadline_expires_at = '' "
                "OR jobs.no_deadline_expires_at >= date('now')) )"
            )
        where = " AND ".join(clauses)
        return (
            """
            WITH student_visible_ranked AS (
                SELECT jobs.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(
                               NULLIF(jobs.official_detail_key, ''),
                               '__job:' || jobs.id
                           )
                           ORDER BY jobs.official_detail_precedence ASC,
                                    jobs.updated_at DESC,
                                    jobs.id DESC
                       ) AS _official_detail_rank
                FROM jobs
                JOIN sources AS source_meta
                  ON source_meta.id = jobs.source_id
                 AND (source_meta.enabled = 1 OR jobs.publication_layer = 'source_unavailable')
                WHERE """
            + where
            + "\n            )\n",
            values,
        )

    def find_job(
        self,
        job_id: int,
        *,
        student_visible: bool = False,
        as_of_date: str | None = None,
    ) -> dict[str, Any] | None:
        if student_visible:
            cte, values = self._student_visible_jobs_cte(as_of_date=as_of_date)
            # When a previously preferred source becomes stale, an old detail
            # link can still resolve to its live, same-detail fallback. This
            # preserves a shared student link without exposing a duplicate.
            with self.connect() as connection:
                row = connection.execute(
                    cte
                    + """
                    SELECT jobs.*
                    FROM student_visible_ranked AS jobs
                    WHERE jobs._official_detail_rank = 1
                      AND (
                          jobs.id = ?
                          OR (
                              jobs.official_detail_key IS NOT NULL
                              AND jobs.official_detail_key <> ''
                              AND jobs.official_detail_key = (
                                  SELECT official_detail_key FROM jobs WHERE id = ?
                              )
                          )
                      )
                    ORDER BY CASE WHEN jobs.id = ? THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    [*values, job_id, job_id, job_id],
                ).fetchone()
            return self._job_row(row) if row else None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT jobs.* FROM jobs WHERE jobs.id = ?", (job_id,)
            ).fetchone()
        return self._job_row(row) if row else None

    def find_public_job(
        self, job_id: int, *, as_of_date: str | None = None
    ) -> dict[str, Any] | None:
        """Return one currently open job that passed the student gate."""
        return self.find_job(
            job_id, student_visible=True, as_of_date=as_of_date
        )

    def list_official_pending_jobs(
        self,
        *,
        category: str | None = None,
        degree: str | None = None,
        province: str | None = None,
        relevance_band: str | None = None,
        publication_layer: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int | None = 20,
        as_of_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List official-source rows isolated from the student publication gate.

        These rows are useful for discovery, but are not current application
        counts.  Requiring a registered source and the persisted official
        verification marker keeps third-party leads and private candidates out
        of the public browse-only view.
        """
        clauses = [
            "jobs.status = 'open'",
            "jobs.publication_status = 'pending_evidence'",
            "jobs.verification_status = 'published_official'",
            "jobs.source_id IS NOT NULL",
            "jobs.status <> 'superseded'",
        ]
        values: list[Any] = []
        if as_of_date is not None:
            date.fromisoformat(as_of_date)
            clauses.append(
                "(jobs.deadline_date >= ? OR "
                "((jobs.deadline_date IS NULL OR jobs.deadline_date = '') AND "
                "(jobs.no_deadline_expires_at IS NULL OR jobs.no_deadline_expires_at = '' "
                "OR jobs.no_deadline_expires_at >= ?)))"
            )
            values.extend([as_of_date, as_of_date])
        if category:
            clauses.append("jobs.category = ?")
            values.append(category)
        if degree:
            clauses.append("jobs.degree_levels_json LIKE ?")
            values.append(f'%"{degree}"%')
        if province:
            clauses.append("(jobs.province = ? OR jobs.provinces_json LIKE ?)")
            values.extend([province, f'%"{province}%"'])
        if relevance_band:
            clauses.append("jobs.relevance_band = ?")
            values.append(relevance_band)
        if publication_layer:
            if publication_layer not in PUBLICATION_LAYERS:
                raise ValueError(f"Unsupported publication layer: {publication_layer}")
            clauses.append("jobs.publication_layer = ?")
            values.append(publication_layer)
        if q:
            clauses.append(
                "(jobs.title LIKE ? OR jobs.employer LIKE ? "
                "OR jobs.canonical_employer_name LIKE ? OR jobs.location LIKE ? "
                "OR jobs.description LIKE ? OR jobs.major_tags_json LIKE ? "
                "OR jobs.field_evidence_json LIKE ?)"
            )
            search = f"%{q.strip()}%"
            values.extend([search] * 7)
        where = " AND ".join(clauses)
        with self.connect() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "JOIN sources AS source_meta ON source_meta.id = jobs.source_id "
                    f"WHERE {where}",
                    values,
                ).fetchone()[0]
            )
            query = (
                "SELECT jobs.* FROM jobs "
                "JOIN sources AS source_meta ON source_meta.id = jobs.source_id "
                f"WHERE {where} "
                "ORDER BY CASE WHEN jobs.deadline_date IS NULL THEN 1 ELSE 0 END, "
                "jobs.deadline_date ASC, jobs.updated_at DESC"
            )
            if page_size is None:
                rows = connection.execute(query, values).fetchall()
            else:
                offset = max(page - 1, 0) * page_size
                rows = connection.execute(
                    f"{query} LIMIT ? OFFSET ?",
                    [*values, page_size, offset],
                ).fetchall()
        return [self._job_row(row) for row in rows], count

    def find_official_pending_job(
        self, job_id: int, *, as_of_date: str | None = None
    ) -> dict[str, Any] | None:
        """Return one official-source browse-only row, never a third-party lead."""
        rows, _ = self.list_official_pending_jobs(
            page_size=None,
            as_of_date=as_of_date,
        )
        return next((job for job in rows if int(job["id"]) == int(job_id)), None)

    def update_derived_job_fields(
        self,
        job_id: int,
        *,
        category: str,
        degree_levels: list[str],
        major_tags: list[str],
        relevance_score: int,
        relevance_band: str,
        status: str,
        publication_status: str,
        publication_basis: dict[str, Any],
    ) -> bool:
        """Refresh rule-derived fields without changing the official job record.

        Taxonomy maintenance must remain traceable, but it must not create a
        student-facing "updated vacancy" event or alter source content dates.
        """
        degrees_json = json.dumps(degree_levels, ensure_ascii=False)
        majors_json = json.dumps(major_tags, ensure_ascii=False)
        with self.transaction() as connection:
            current = connection.execute(
                """
                SELECT category, degree_levels_json, major_tags_json,
                       relevance_score, relevance_band, status,
                       publication_status, publication_basis_json
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if current is None:
                return False
            derived = {
                "category": category,
                "degree_levels_json": degrees_json,
                "major_tags_json": majors_json,
                "relevance_score": relevance_score,
                "relevance_band": relevance_band,
                "status": status,
                "publication_status": publication_status,
                "publication_basis_json": json.dumps(
                    publication_basis, ensure_ascii=False
                ),
            }
            if all(current[key] == value for key, value in derived.items()):
                return False
            connection.execute(
                """
                UPDATE jobs
                SET category = ?, degree_levels_json = ?, major_tags_json = ?,
                    relevance_score = ?, relevance_band = ?, status = ?,
                    publication_status = ?, publication_basis_json = ?
                WHERE id = ?
                """,
                (
                    category,
                    degrees_json,
                    majors_json,
                    relevance_score,
                    relevance_band,
                    status,
                    publication_status,
                    derived["publication_basis_json"],
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                VALUES (?, 'reclassified', ?, ?)
                """,
                (
                    job_id,
                    utc_now(),
                    json.dumps(
                        {"before": dict(current), "after": derived},
                        ensure_ascii=False,
                    ),
                ),
            )
        return True

    def update_job_field_evidence(
        self,
        job_id: int,
        field_evidence: dict[str, Any],
    ) -> bool:
        """Repair structured official evidence without changing source identity."""
        payload = json.dumps(field_evidence, ensure_ascii=False)
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT field_evidence_json FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if current is None:
                return False
            if current["field_evidence_json"] == payload:
                return False
            now = utc_now()
            connection.execute(
                "UPDATE jobs SET field_evidence_json = ?, updated_at = ? WHERE id = ?",
                (payload, now, job_id),
            )
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                VALUES (?, 'evidence_repaired', ?, ?)
                """,
                (job_id, now, json.dumps({"field_evidence": field_evidence}, ensure_ascii=False)),
            )
        return True

    def update_artifact_candidate_field_evidence(
        self,
        candidate_id: int,
        field_evidence: dict[str, Any],
    ) -> bool:
        """Backfill canonical row evidence while preserving review state."""
        payload = json.dumps(field_evidence, ensure_ascii=False)
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT field_evidence_json FROM artifact_job_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if current is None:
                return False
            if current["field_evidence_json"] == payload:
                return False
            connection.execute(
                """
                UPDATE artifact_job_candidates
                SET field_evidence_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (payload, utc_now(), candidate_id),
            )
        return True

    def update_job_normalization(
        self,
        job_id: int,
        *,
        canonical_employer_id: str | None,
        canonical_employer_name: str | None,
        parent_employer_name: str | None,
        location: str | None = None,
        provinces: list[str] | None = None,
        province: str | None,
        city: str | None,
        country_or_region: str | None,
        location_confidence: str,
        location_evidence: str | None,
    ) -> bool:
        """Refresh deterministic registry/location fields without a vacancy event."""
        values = {
            "canonical_employer_id": canonical_employer_id,
            "canonical_employer_name": canonical_employer_name,
            "parent_employer_name": parent_employer_name,
            "provinces_json": json.dumps(provinces or [], ensure_ascii=False),
            "province": province,
            "city": city,
            "country_or_region": country_or_region,
            "location_confidence": location_confidence,
            "location_evidence": location_evidence,
        }
        with self.transaction() as connection:
            current = connection.execute(
                """
                SELECT location, canonical_employer_id, canonical_employer_name,
                       parent_employer_name, province, provinces_json, city, country_or_region,
                       location_confidence, location_evidence
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if current is None:
                return False
            if location is None:
                values.pop("location", None)
            else:
                values["location"] = location
            if all(current[key] == value for key, value in values.items()):
                return False
            if "location" in values:
                connection.execute(
                    """
                    UPDATE jobs
                    SET location = ?, canonical_employer_id = ?,
                        canonical_employer_name = ?, parent_employer_name = ?,
                        province = ?, provinces_json = ?, city = ?, country_or_region = ?,
                        location_confidence = ?, location_evidence = ?
                    WHERE id = ?
                    """,
                    (
                        values["location"],
                        values["canonical_employer_id"],
                        values["canonical_employer_name"],
                        values["parent_employer_name"],
                        values["province"],
                        values["provinces_json"],
                        values["city"],
                        values["country_or_region"],
                        values["location_confidence"],
                        values["location_evidence"],
                        job_id,
                    ),
                )
                return True
            connection.execute(
                """
                UPDATE jobs
                SET canonical_employer_id = ?, canonical_employer_name = ?,
                    parent_employer_name = ?, province = ?, provinces_json = ?, city = ?,
                    country_or_region = ?, location_confidence = ?,
                    location_evidence = ?
                WHERE id = ?
                """,
                (
                    values["canonical_employer_id"],
                    values["canonical_employer_name"],
                    values["parent_employer_name"],
                    values["province"],
                    values["provinces_json"],
                    values["city"],
                    values["country_or_region"],
                    values["location_confidence"],
                    values["location_evidence"],
                    job_id,
                ),
            )
            return True

    def list_latest_crawl_runs(self) -> list[dict[str, Any]]:
        """Return the newest run for each source, including failed attempts.

        A previous successful scan must not mask a newer source failure. Coverage
        therefore reads the newest run rather than the last successful one.
        """
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT crawl_runs.*
                FROM crawl_runs
                JOIN (
                    SELECT source_id, MAX(id) AS latest_id
                    FROM crawl_runs
                    GROUP BY source_id
                ) AS latest ON latest.latest_id = crawl_runs.id
                ORDER BY crawl_runs.source_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_crawl_runs(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Return recent source runs, including failed and interrupted runs."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT crawl_runs.*, sources.name AS source_name
                FROM crawl_runs
                LEFT JOIN sources ON sources.id = crawl_runs.source_id
                ORDER BY crawl_runs.id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 10000)),),
            ).fetchall()
        return [dict(row) for row in rows]
        return True

    def list_jobs(
        self,
        *,
        category: str | None = None,
        degree: str | None = None,
        province: str | None = None,
        relevance_band: str | None = None,
        publication_layer: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int | None = 20,
        only_open: bool = True,
        student_visible: bool = True,
        as_of_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        values: list[Any] = []
        base_query = "jobs"
        cte = ""
        if only_open:
            # ``status`` is a materialized current-state field. A historical
            # read must still inspect rows the worker has since marked
            # expired; the dated deadline/freshness predicates decide whether
            # that row was visible on the requested date.
            clauses.append(
                "jobs.status IN ('open', 'expired')"
                if as_of_date is not None
                else "jobs.status = 'open'"
            )
        if student_visible:
            cte, cte_values = self._student_visible_jobs_cte(
                as_of_date=as_of_date
            )
            base_query = "student_visible_ranked AS jobs"
            values.extend(cte_values)
            clauses.append("jobs._official_detail_rank = 1")
        if category:
            clauses.append("jobs.category = ?")
            values.append(category)
        if degree:
            clauses.append("jobs.degree_levels_json LIKE ?")
            values.append(f'%"{degree}"%')
        if province:
            clauses.append(
                "(jobs.province = ? OR jobs.provinces_json LIKE ?)"
            )
            values.extend([province, f'%"{province}"%'])
        if relevance_band:
            clauses.append("jobs.relevance_band = ?")
            values.append(relevance_band)
        if publication_layer:
            if publication_layer not in PUBLICATION_LAYERS:
                raise ValueError(f"Unsupported publication layer: {publication_layer}")
            clauses.append("jobs.publication_layer = ?")
            values.append(publication_layer)
        if q:
            clauses.append(
                "(jobs.title LIKE ? OR jobs.employer LIKE ? "
                "OR jobs.canonical_employer_name LIKE ? OR jobs.location LIKE ? "
                "OR jobs.description LIKE ? OR jobs.major_tags_json LIKE ? "
                "OR jobs.field_evidence_json LIKE ?)"
            )
            search = f"%{q.strip()}%"
            values.extend([search, search, search, search, search, search, search])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            count = connection.execute(
                cte + f"SELECT COUNT(*) FROM {base_query}{where}", values
            ).fetchone()[0]
            query = f"""
                {cte}SELECT jobs.* FROM {base_query}{where}
                ORDER BY
                    CASE jobs.relevance_band
                        WHEN '强相关' THEN 1
                        WHEN '相关机会' THEN 2
                        ELSE 3
                    END,
                    CASE WHEN jobs.deadline_date IS NULL THEN 1 ELSE 0 END,
                    jobs.deadline_date ASC,
                    jobs.updated_at DESC
            """
            if page_size is None:
                rows = connection.execute(query, values).fetchall()
            else:
                offset = max(page - 1, 0) * page_size
                rows = connection.execute(
                    f"{query} LIMIT ? OFFSET ?",
                    [*values, page_size, offset],
                ).fetchall()
        return [self._job_row(row) for row in rows], int(count)

    def list_categories(self, *, as_of_date: str | None = None) -> list[dict[str, Any]]:
        cte, values = self._student_visible_jobs_cte(as_of_date=as_of_date)
        with self.connect() as connection:
            rows = connection.execute(
                cte
                + """
                SELECT jobs.category, COUNT(*) AS count
                FROM student_visible_ranked AS jobs
                WHERE jobs._official_detail_rank = 1
                GROUP BY jobs.category
                ORDER BY count DESC, jobs.category
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def jobs_for_report(
        self,
        report_date: str,
        timezone_name: str = "UTC",
    ) -> dict[str, list[dict[str, Any]]]:
        """Return changes inside one local calendar day.

        Event timestamps are stored in UTC. Converting the requested report date
        into UTC bounds prevents an Asia/Shanghai daily report from losing changes
        made between local midnight and 08:00.
        """
        report_day = date.fromisoformat(report_date)
        local_zone = ZoneInfo(timezone_name)
        start = datetime.combine(report_day, time.min, tzinfo=local_zone)
        end = start + timedelta(days=1)
        start_utc = start.astimezone(timezone.utc).replace(microsecond=0)
        end_utc = end.astimezone(timezone.utc).replace(microsecond=0)
        start_value = start_utc.isoformat().replace("+00:00", "Z")
        end_value = end_utc.isoformat().replace("+00:00", "Z")
        cte, freshness_values = self._student_visible_jobs_cte(
            as_of_date=report_date
        )
        with self.connect() as connection:
            rows = connection.execute(
                cte
                + """
                SELECT DISTINCT jobs.*, job_events.event_type, job_events.occurred_at
                FROM job_events
                JOIN student_visible_ranked AS jobs ON jobs.id = job_events.job_id
                WHERE job_events.occurred_at >= ?
                  AND job_events.occurred_at < ?
                  AND jobs._official_detail_rank = 1
                ORDER BY jobs.relevance_score DESC, jobs.deadline_date ASC
                """,
                (*freshness_values, start_value, end_value),
            ).fetchall()
        created: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        for row in rows:
            job = self._job_row(row)
            if row["event_type"] == "created":
                created.append(job)
            elif row["event_type"] == "updated":
                updated.append(job)
        return {"new": created, "updated": updated}

    def upcoming_deadlines(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        cte, values = self._student_visible_jobs_cte(as_of_date=start_date)
        with self.connect() as connection:
            rows = connection.execute(
                cte
                + """
                SELECT jobs.*
                FROM student_visible_ranked AS jobs
                WHERE jobs._official_detail_rank = 1
                  AND jobs.deadline_date IS NOT NULL
                  AND jobs.deadline_date >= ?
                  AND jobs.deadline_date <= ?
                ORDER BY jobs.deadline_date ASC, jobs.relevance_score DESC
                """,
                (*values, start_date, end_date),
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def count_open_jobs(self, *, as_of_date: str | None = None) -> int:
        cte, values = self._student_visible_jobs_cte(as_of_date=as_of_date)
        with self.connect() as connection:
            return int(
                connection.execute(
                    cte
                    + "SELECT COUNT(*) FROM student_visible_ranked AS jobs "
                    "WHERE jobs._official_detail_rank = 1",
                    values,
                ).fetchone()[0]
            )

    def record_service_heartbeat(
        self,
        service_name: str,
        status: str,
        detail: str = "",
    ) -> None:
        """Persist a liveness signal for a long-running service such as worker."""
        if service_name.endswith("-browser"):
            detail = f"policy_sha256={publication_policy_digest()}; {detail}"
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO service_heartbeats (service_name, status, detail, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(service_name) DO UPDATE SET
                    status=excluded.status,
                    detail=excluded.detail,
                    updated_at=excluded.updated_at
                """,
                (service_name, status, detail[:500], utc_now()),
            )

    def get_service_heartbeat(self, service_name: str) -> dict[str, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT service_name, status, detail, updated_at "
                "FROM service_heartbeats WHERE service_name = ?",
                (service_name,),
            ).fetchone()
        return dict(row) if row else None

    def count_jobs_for_source(self, source_id: str) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0]
            )

    def delete_jobs_for_source(self, source_id: str) -> int:
        """Remove only one source's generated records and their change events.

        The source configuration itself remains intact. SQLite foreign keys cascade
        the associated job events, so a later sync can rebuild this source cleanly.
        """
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE source_id = ?",
                (source_id,),
            )
            return int(cursor.rowcount)

    def student_visible_external_ids_for_source(self, source_id: str) -> set[str]:
        """Return open student-facing external identifiers for a source.

        This deliberately ignores the source's collector enabled flag.  A
        transition needs to compare a retired snapshot with a staged dynamic
        source before the latter is enabled for normal scheduled collection.
        """

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT external_id
                FROM jobs
                WHERE source_id = ?
                  AND status = 'open'
                  AND (
                    publication_status IN ('student_eligible', 'unrestricted_eligible', 'source_unavailable')
                    OR publication_layer IN ('exact_match', 'broad_geoscience_match',
                                              'incomplete_but_basic_match', 'source_unavailable')
                  )
                  AND country_or_region IN ('中国大陆', '中国')
                  AND external_id IS NOT NULL
                  AND external_id <> ''
                """,
                (source_id,),
            ).fetchall()
        return {str(row["external_id"]) for row in rows}

    def withdraw_missing_source_jobs(
        self,
        source_id: str,
        current_external_ids: set[str],
        *,
        reason: str,
    ) -> int:
        """Keep rows open: one scan without a row is not closure evidence."""

        return 0

    def withdraw_stale_source_jobs(
        self,
        source_id: str,
        *,
        reason: str,
    ) -> int:
        """Compatibility no-op: freshness never changes lifecycle state."""

        return 0

    def complete_source_transition(
        self,
        *,
        retired_source_id: str,
        active_source_id: str,
        transition_name: str,
    ) -> int:
        """Atomically retire one visible snapshot after a successor has synced.

        The old jobs remain in the database with their original evidence and
        events.  They are marked superseded, so an administrator can audit or
        roll back the source switch without presenting duplicate vacancies to
        students.
        """

        now = utc_now()
        with self.transaction() as connection:
            retired = connection.execute(
                "SELECT id FROM sources WHERE id = ?", (retired_source_id,)
            ).fetchone()
            active = connection.execute(
                "SELECT id FROM sources WHERE id = ?", (active_source_id,)
            ).fetchone()
            if retired is None or active is None:
                raise ValueError("Both transition sources must be registered")
            rows = connection.execute(
                "SELECT id, title, external_id FROM jobs WHERE source_id = ? AND status = 'open'",
                (retired_source_id,),
            ).fetchall()
            successors = {
                str(row["external_id"]): int(row["id"])
                for row in connection.execute(
                    "SELECT id, external_id FROM jobs WHERE source_id = ? AND status = 'open'",
                    (active_source_id,),
                ).fetchall()
                if str(row["external_id"] or "").strip()
            }
            connection.execute(
                "UPDATE sources SET enabled = 0, updated_at = ? WHERE id = ?",
                (now, retired_source_id),
            )
            connection.execute(
                "UPDATE sources SET enabled = 1, updated_at = ? WHERE id = ?",
                (now, active_source_id),
            )
            if rows:
                connection.executemany(
                    "UPDATE jobs SET status = 'superseded', superseded_successor_job_id = ?, "
                    "superseded_reason = ?, superseded_at = ?, updated_at = ? WHERE id = ?",
                    [
                        (
                            successors.get(str(row["external_id"] or "").strip()),
                            "source_transition",
                            now,
                            now,
                            int(row["id"]),
                        )
                        for row in rows
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                    VALUES (?, 'superseded', ?, ?)
                    """,
                    [
                        (
                            int(row["id"]),
                            now,
                            json.dumps(
                                {
                                    "transition": transition_name,
                                    "successor_source_id": active_source_id,
                                    "external_id": row["external_id"],
                                },
                                ensure_ascii=False,
                            ),
                        )
                        for row in rows
                    ],
                )
            return len(rows)

    def delete_job(self, job_id: int) -> bool:
        """Remove one exact invalid record and its cascade-owned change events.

        This deliberately accepts a primary key rather than a fuzzy title or a
        source-wide selector. It is used only after an operator has previewed a
        concrete audit finding, so valid records from the same official source
        remain untouched.
        """
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return bool(cursor.rowcount)

    def create_candidate_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
        """Store a third-party discovery clue outside the public jobs table."""
        required = ("lead_provider", "lead_url", "title")
        missing = [field for field in required if not str(lead.get(field, "")).strip()]
        if missing:
            raise ValueError(f"Candidate lead is missing: {', '.join(missing)}")
        lead_url = str(lead["lead_url"]).strip()
        if not is_http_url(lead_url):
            raise ValueError("Candidate lead lead_url must be an HTTP(S) URL")
        official_url = self._optional_text(lead.get("official_url"))
        if official_url and not is_http_url(official_url):
            raise ValueError("Candidate lead official_url must be an HTTP(S) URL")
        discovery_source_id = self._optional_text(lead.get("discovery_source_id"))
        official_source_id = self._optional_text(lead.get("official_source_id"))
        metadata = lead.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Candidate lead metadata must be an object")
        identity_hint = self._optional_text(
            metadata.get("position_code") or metadata.get("locator")
        )
        fingerprint = lead_fingerprint(
            lead_url,
            title=str(lead["title"]),
            employer_hint=str(lead.get("employer_hint") or ""),
            identity_hint=identity_hint or "",
        )
        now = utc_now()
        official_application_url = self._optional_text(
            lead.get("official_application_url") or metadata.get("application_url")
        )
        if official_application_url and not is_http_url(official_application_url):
            raise ValueError("Candidate lead official_application_url must be an HTTP(S) URL")
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM candidate_leads WHERE lead_fingerprint = ? "
                "ORDER BY id LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                self._record_candidate_lead_mention(
                    connection,
                    int(existing["id"]),
                    discovery_source_id=discovery_source_id,
                    lead_url=lead_url,
                    metadata={"lead_provider": str(lead["lead_provider"]).strip()},
                    captured_at=now,
                )
                return self._lead_row(existing)
            assessment = self._assess_official_url(
                connection,
                official_url,
                official_source_id=official_source_id,
            )
            cursor = connection.execute(
                """
                INSERT INTO candidate_leads (
                    lead_provider, lead_url, title, employer_hint, location_hint,
                    province_hint, published_date, official_url, discovery_source_id,
                    lead_fingerprint, official_source_id, official_domain_status,
                    official_checked_at, trust_grade, official_table_url,
                    third_party_table_url, official_application_url, conversion_status,
                    verification_status, verification_note,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unresolved', 'candidate', ?, ?, ?, ?)
                """,
                (
                    str(lead["lead_provider"]).strip(),
                    lead_url,
                    str(lead["title"]).strip(),
                    self._optional_text(lead.get("employer_hint")),
                    self._optional_text(lead.get("location_hint")),
                    self._optional_text(lead.get("province_hint")),
                    self._optional_text(lead.get("published_date")),
                    official_url,
                    discovery_source_id,
                    fingerprint,
                    assessment.get("source_id"),
                    assessment["status"] if official_url else "unverified",
                    now if official_url else None,
                    normalize_trust_grade(lead.get("trust_grade"), default="D"),
                    self._optional_text(lead.get("official_table_url")),
                    self._optional_text(lead.get("third_party_table_url")),
                    official_application_url,
                    self._optional_text(lead.get("verification_note")) or "",
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            lead_id = int(cursor.lastrowid)
            self._record_candidate_lead_event(
                connection,
                lead_id,
                event_type="created",
                from_status=None,
                to_status="candidate",
                note=self._optional_text(lead.get("verification_note")) or "",
                payload={"lead_provider": str(lead["lead_provider"]).strip()},
                occurred_at=now,
            )
            self._record_candidate_lead_mention(
                connection,
                lead_id,
                discovery_source_id=discovery_source_id,
                lead_url=lead_url,
                metadata={"lead_provider": str(lead["lead_provider"]).strip()},
                captured_at=now,
            )
            row = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        return self._lead_row(row)

    @staticmethod
    def _record_candidate_lead_mention(
        connection: sqlite3.Connection,
        lead_id: int,
        *,
        discovery_source_id: str | None,
        lead_url: str,
        metadata: dict[str, Any],
        captured_at: str,
    ) -> None:
        """Retain every private discovery channel that pointed to one lead."""
        connection.execute(
            """
            INSERT INTO candidate_lead_mentions (
                lead_id, discovery_source_id, lead_url, normalized_url,
                captured_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(lead_id, discovery_source_id, normalized_url) DO UPDATE SET
                captured_at = excluded.captured_at,
                metadata_json = excluded.metadata_json
            """,
            (
                lead_id,
                discovery_source_id,
                lead_url,
                normalize_lead_url(lead_url),
                captured_at,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

    def get_candidate_lead(self, lead_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        return self._lead_row(row) if row else None

    @staticmethod
    def _assess_official_url(
        connection: sqlite3.Connection,
        official_url: str | None,
        *,
        official_source_id: str | None = None,
    ) -> dict[str, Any]:
        if not official_url:
            return {
                "status": "unverified",
                "source_id": official_source_id,
                "host": "",
            }
        source_rows = connection.execute(
            "SELECT * FROM sources ORDER BY id"
        ).fetchall()
        records: list[dict[str, Any]] = []
        for row in source_rows:
            item = dict(row)
            item["config"] = json.loads(item.pop("config_json", "{}"))
            records.append(item)
        return official_domain_assessment(
            official_url,
            records,
            official_source_id=official_source_id,
        )

    @staticmethod
    def _assess_registered_application_url(
        connection: sqlite3.Connection,
        application_url: str | None,
        *,
        preferred_source_id: str | None = None,
    ) -> dict[str, Any]:
        """Require a student-facing application URL to belong to a registered source.

        Third-party lead records may carry an unverified application hint while
        they are private.  Once the lead is marked official-content-verified,
        the hint must resolve to a registered official host (or be covered by
        the same explicit manual-domain approval as the official detail URL).
        """
        if not application_url:
            return {"status": "unverified", "source_id": preferred_source_id, "host": ""}
        source_rows = connection.execute("SELECT * FROM sources ORDER BY id").fetchall()
        records: list[dict[str, Any]] = []
        for row in source_rows:
            item = dict(row)
            item["config"] = json.loads(item.pop("config_json", "{}"))
            records.append(item)
        ordered_ids: list[str] = []
        if preferred_source_id:
            ordered_ids.append(preferred_source_id)
        ordered_ids.extend(
            str(item.get("id") or "")
            for item in records
            if str(item.get("id") or "") not in ordered_ids
        )
        assessments = [
            official_domain_assessment(
                application_url,
                records,
                official_source_id=source_id,
            )
            for source_id in ordered_ids
            if source_id
        ]
        for assessment in assessments:
            if assessment.get("status") == "registered_source_match":
                return assessment
        return assessments[0] if assessments else {
            "status": "manual_review_required",
            "source_id": preferred_source_id,
            "host": (urlparse(application_url).hostname or "").lower().rstrip("."),
        }

    def list_candidate_leads(
        self,
        verification_status: str | None = None,
        *,
        discovery_source_id: str | None = None,
        province: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM candidate_leads"
        values: list[Any] = []
        clauses: list[str] = []
        if verification_status:
            clauses.append("verification_status = ?")
            values.append(verification_status)
        if discovery_source_id:
            clauses.append("discovery_source_id = ?")
            values.append(discovery_source_id)
        if province:
            clauses.append("province_hint = ?")
            values.append(province)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        values.append(max(1, min(limit, 5000)))
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._lead_row(row) for row in rows]

    def list_candidate_lead_mentions(self, lead_id: int) -> list[dict[str, Any]]:
        """Return private source mentions for one deduplicated lead."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_lead_mentions
                WHERE lead_id = ?
                ORDER BY captured_at DESC, id DESC
                """,
                (lead_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def list_candidate_lead_events(self, lead_id: int) -> list[dict[str, Any]]:
        """Return private status history for one candidate lead."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_lead_events
                WHERE lead_id = ?
                ORDER BY occurred_at ASC, id ASC
                """,
                (lead_id,),
            ).fetchall()
        return [self._candidate_lead_event_row(row) for row in rows]

    def update_candidate_lead(
        self,
        lead_id: int,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Advance an internal lead, never publishing it implicitly."""
        current = self.get_candidate_lead(lead_id)
        if current is None:
            return None
        editable = {
            "employer_hint",
            "location_hint",
            "province_hint",
            "published_date",
            "official_url",
            "official_application_url",
            "official_source_id",
            "official_domain_status",
            "verification_note",
            "metadata",
            "verification_status",
            "trust_grade",
            "official_table_url",
            "third_party_table_url",
            "conversion_status",
        }
        unexpected = set(changes).difference(editable)
        if unexpected:
            raise ValueError(f"Unsupported lead fields: {', '.join(sorted(unexpected))}")
        next_status = str(
            changes.get("verification_status", current["verification_status"])
        ).strip()
        if next_status == "published":
            raise ValueError("Use the verified-lead publication action instead")
        official_url = self._optional_text(
            changes.get("official_url", current.get("official_url"))
        )
        official_application_url = self._optional_text(
            changes.get(
                "official_application_url",
                current.get("official_application_url"),
            )
        )
        if official_application_url and not is_http_url(official_application_url):
            raise ValueError("Candidate lead official_application_url must be an HTTP(S) URL")
        verification_note = self._optional_text(
            changes.get("verification_note", current.get("verification_note"))
        ) or ""
        next_status, normalized_official_url = validate_candidate_lead_transition(
            current["verification_status"],
            next_status,
            official_url=official_url,
            verification_note=verification_note,
        )
        official_url = normalized_official_url or None
        official_source_id = self._optional_text(
            changes.get("official_source_id", current.get("official_source_id"))
        )
        requested_domain_status = self._optional_text(
            changes.get("official_domain_status")
        )
        if requested_domain_status and requested_domain_status not in OFFICIAL_DOMAIN_STATUSES:
            raise ValueError("Unsupported official_domain_status")
        metadata = changes.get("metadata", current.get("metadata", {}))
        if not isinstance(metadata, dict):
            raise ValueError("Candidate lead metadata must be an object")
        fields = {
            "employer_hint": self._optional_text(
                changes.get("employer_hint", current.get("employer_hint"))
            ),
            "location_hint": self._optional_text(
                changes.get("location_hint", current.get("location_hint"))
            ),
            "province_hint": self._optional_text(
                changes.get("province_hint", current.get("province_hint"))
            ),
            "published_date": self._optional_text(
                changes.get("published_date", current.get("published_date"))
            ),
            "official_url": official_url,
            "verification_status": next_status,
            "verification_note": verification_note,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "updated_at": utc_now(),
        }
        with self.transaction() as connection:
            assessment = self._assess_official_url(
                connection,
                official_url,
                official_source_id=official_source_id,
            )
            domain_status = requested_domain_status or (
                assessment["status"] if official_url else "unverified"
            )
            if domain_status == "manual_review_approved" and not verification_note:
                raise ValueError(
                    "Manual official-domain approval requires a verification_note"
                )
            if (
                domain_status != "manual_review_approved"
                and domain_status != assessment["status"]
            ):
                raise ValueError(
                    "official_domain_status must match the current official URL "
                    "assessment unless manually approved"
                )
            if (
                next_status in {"official_content_verified", "published"}
                and assessment["status"] == "source_domain_mismatch"
                and domain_status != "manual_review_approved"
            ):
                raise ValueError(
                    "Official URL host does not match the selected registered source"
                )
            if next_status == "official_content_verified" and domain_status not in {
                "registered_source_match",
                "manual_review_approved",
            }:
                raise ValueError(
                    "Official-content verification requires a registered source match "
                    "or explicit manual-domain approval"
                )
            if next_status in {"official_content_verified", "published"} and official_application_url:
                application_assessment = self._assess_registered_application_url(
                    connection,
                    official_application_url,
                    preferred_source_id=official_source_id,
                )
                official_host = (urlparse(official_url or "").hostname or "").lower().rstrip(".")
                application_host = (
                    urlparse(official_application_url).hostname or ""
                ).lower().rstrip(".")
                same_manually_approved_host = (
                    domain_status == "manual_review_approved"
                    and bool(official_host)
                    and application_host == official_host
                )
                if (
                    application_assessment.get("status") != "registered_source_match"
                    and not same_manually_approved_host
                ):
                    raise ValueError(
                        "Official-content verification requires a registered official "
                        "application URL; third-party application links are not allowed"
                    )
            checked_at = utc_now() if official_url else None
            connection.execute(
                """
                UPDATE candidate_leads
                    SET employer_hint = ?, location_hint = ?, province_hint = ?,
                    published_date = ?, official_url = ?, official_source_id = ?,
                    official_domain_status = ?, official_checked_at = ?,
                    trust_grade = ?, official_table_url = ?, third_party_table_url = ?,
                    official_application_url = ?,
                    conversion_status = ?, verification_status = ?, verification_note = ?, metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    fields["employer_hint"],
                    fields["location_hint"],
                    fields["province_hint"],
                    fields["published_date"],
                    fields["official_url"],
                    official_source_id,
                    domain_status,
                    checked_at,
                    normalize_trust_grade(changes.get("trust_grade", current.get("trust_grade")), default="D"),
                    self._optional_text(changes.get("official_table_url", current.get("official_table_url"))),
                    self._optional_text(changes.get("third_party_table_url", current.get("third_party_table_url"))),
                    official_application_url,
                    self._optional_text(changes.get("conversion_status", current.get("conversion_status"))) or "unresolved",
                    fields["verification_status"],
                    fields["verification_note"],
                    fields["metadata_json"],
                    fields["updated_at"],
                    lead_id,
                ),
            )
            if current["verification_status"] != next_status:
                self._record_candidate_lead_event(
                    connection,
                    lead_id,
                    event_type="status_changed",
                    from_status=current["verification_status"],
                    to_status=next_status,
                    note=verification_note,
                    payload={},
                    occurred_at=fields["updated_at"],
                )
            row = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        return self._lead_row(row)

    def mark_candidate_lead_published(
        self,
        lead_id: int,
        job_id: int,
    ) -> dict[str, Any] | None:
        """Link a verified lead to its published official job record."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
            if row is None:
                return None
            if row["verification_status"] != "official_content_verified":
                raise ValueError("Lead has not passed official-content verification")
            if row["official_domain_status"] not in {
                "registered_source_match",
                "manual_review_approved",
            }:
                raise ValueError(
                    "Lead has no registered-source match or explicit manual-domain approval"
                )
            if connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
                raise ValueError("Published lead references an unknown job_id")
            validate_candidate_lead_transition(
                row["verification_status"],
                "published",
                official_url=row["official_url"],
                verification_note=row["verification_note"],
            )
            now = utc_now()
            connection.execute(
                """
                UPDATE candidate_leads
                SET verification_status = 'published', published_job_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (job_id, now, lead_id),
            )
            self._record_candidate_lead_event(
                connection,
                lead_id,
                event_type="published",
                from_status=row["verification_status"],
                to_status="published",
                note=str(row["verification_note"] or ""),
                payload={"published_job_id": job_id},
                occurred_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        return self._lead_row(updated)

    @staticmethod
    def _backfill_job_evidence(connection: sqlite3.Connection) -> None:
        """Index legacy official links as evidence without changing job content."""
        rows = connection.execute(
            """
            SELECT jobs.id, jobs.source_url, jobs.official_evidence_url
            FROM jobs
            WHERE NOT EXISTS (
                SELECT 1
                FROM job_evidence
                WHERE job_evidence.job_id = jobs.id
                  AND job_evidence.verification_status = 'verified'
                  AND job_evidence.evidence_type IN ('official_page', 'official_record')
            )
            """
        ).fetchall()
        for row in rows:
            evidence_url = str(
                row["official_evidence_url"] or row["source_url"] or ""
            ).strip()
            if not is_http_url(evidence_url):
                continue
            Database._upsert_job_evidence(
                connection,
                int(row["id"]),
                {
                    "evidence_type": "official_page",
                    "field_name": "job_record",
                    "evidence_url": evidence_url,
                    "locator": "official_evidence_url",
                    "verification_status": "verified",
                    "metadata": {"origin": "phase_1_migration"},
                },
            )

    @staticmethod
    def _backfill_candidate_lead_events(connection: sqlite3.Connection) -> None:
        """Give pre-Phase-1 private leads one immutable status snapshot event."""
        rows = connection.execute(
            """
            SELECT candidate_leads.*
            FROM candidate_leads
            WHERE NOT EXISTS (
                SELECT 1
                FROM candidate_lead_events
                WHERE candidate_lead_events.lead_id = candidate_leads.id
            )
            """
        ).fetchall()
        for row in rows:
            Database._record_candidate_lead_event(
                connection,
                int(row["id"]),
                event_type="status_snapshot",
                from_status=None,
                to_status=str(row["verification_status"]),
                note=str(row["verification_note"] or ""),
                payload={"origin": "phase_1_migration"},
                occurred_at=str(row["updated_at"] or utc_now()),
            )

    @staticmethod
    def _record_candidate_lead_event(
        connection: sqlite3.Connection,
        lead_id: int,
        *,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        note: str,
        payload: dict[str, Any],
        occurred_at: str,
    ) -> None:
        event_identity = {
            "lead_id": lead_id,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "note": note,
            "payload": payload,
            "occurred_at": occurred_at,
        }
        event_key = hashlib.sha256(
            json.dumps(event_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO candidate_lead_events (
                lead_id, event_key, event_type, from_status, to_status,
                note, payload_json, occurred_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_key) DO NOTHING
            """,
            (
                lead_id,
                event_key,
                event_type,
                from_status,
                to_status,
                note,
                json.dumps(payload, ensure_ascii=False),
                occurred_at,
            ),
        )

    def expire_jobs_before(self, today: str) -> int:
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, title, deadline_date
                FROM jobs
                WHERE status = 'open'
                  AND deadline_date IS NOT NULL
                  AND deadline_date < ?
                """,
                (today,),
            ).fetchall()
            if not rows:
                return 0
            connection.executemany(
                """
                UPDATE jobs
                SET status = 'expired', updated_at = ?
                WHERE id = ? AND status = 'open'
                """,
                [(now, int(row["id"])) for row in rows],
            )
            connection.executemany(
                """
                INSERT INTO job_events (job_id, event_type, occurred_at, payload_json)
                VALUES (?, 'expired', ?, ?)
                """,
                [
                    (
                        int(row["id"]),
                        now,
                        json.dumps(
                            {
                                "title": row["title"],
                                "deadline_date": row["deadline_date"],
                            },
                            ensure_ascii=False,
                        ),
                    )
                    for row in rows
                ],
            )
            return len(rows)

    def count_job_events_on_local_day(
        self,
        event_type: str,
        local_date: str,
        timezone_name: str = "UTC",
    ) -> int:
        """Count immutable job events in a configured local calendar day."""
        report_day = date.fromisoformat(local_date)
        local_zone = ZoneInfo(timezone_name)
        start = datetime.combine(report_day, time.min, tzinfo=local_zone)
        end = start + timedelta(days=1)
        start_value = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        end_value = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM job_events
                    WHERE event_type = ?
                      AND occurred_at >= ?
                      AND occurred_at < ?
                    """,
                    (event_type, start_value, end_value),
                ).fetchone()[0]
            )

    def get_daily_report(self, report_date: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM daily_reports WHERE report_date = ?",
                (report_date,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        payload.update(
            {
                "report_date": row["report_date"],
                "published_at": row["published_at"],
                "delivery_status": row["delivery_status"],
                "delivery_error": row["delivery_error"],
            }
        )
        return payload

    def latest_daily_report(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_date FROM daily_reports ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
        return self.get_daily_report(row["report_date"]) if row else None

    def list_report_dates(self, limit: int = 30) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT report_date
                FROM daily_reports
                ORDER BY report_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["report_date"]) for row in rows]

    def save_daily_report(
        self, report_date: str, payload: dict[str, Any], delivery_status: str = "pending"
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO daily_reports (
                    report_date, published_at, payload_json, delivery_status, delivery_error
                )
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(report_date) DO NOTHING
                """,
                (
                    report_date,
                    utc_now(),
                    json.dumps(payload, ensure_ascii=False),
                    delivery_status,
                ),
            )

    def update_delivery_status(
        self, report_date: str, status: str, error: str | None = None
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE daily_reports
                SET delivery_status = ?, delivery_error = ?
                WHERE report_date = ?
                """,
                (status, error, report_date),
            )

    def recent_crawl_failures(self, limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT crawl_runs.*, sources.name AS source_name
                FROM crawl_runs
                LEFT JOIN sources ON sources.id = crawl_runs.source_id
                WHERE crawl_runs.status = 'failed'
                  AND crawl_runs.id = (
                      SELECT MAX(latest.id)
                      FROM crawl_runs AS latest
                      WHERE latest.source_id = crawl_runs.source_id
                  )
                ORDER BY crawl_runs.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _crawl_run_cutoff(max_age_seconds: int) -> str:
        if max_age_seconds < 1:
            raise ValueError("max_age_seconds must be greater than zero")
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_source_artifact_urls(
        source: dict[str, Any],
        artifact: dict[str, Any],
    ) -> None:
        """Keep parent pages and downloads inside explicit official host lists."""
        config = source.get("config", {})
        if not isinstance(config, dict):  # pragma: no cover - source contract prevents this
            raise ValueError("Registered source config must be an object")
        homepage_host = urlparse(str(source.get("homepage_url") or "")).hostname
        page_hosts = {
            str(host).strip().lower()
            for host in config.get("allowed_hosts", [])
            if str(host).strip()
        }
        if homepage_host:
            page_hosts.add(homepage_host.lower())
        attachment_hosts = set(page_hosts)
        attachment_hosts.update(
            str(host).strip().lower()
            for host in config.get("attachment_allowed_hosts", [])
            if str(host).strip()
        )
        parent_host = urlparse(artifact["parent_url"]).hostname
        file_host = urlparse(artifact["artifact_url"]).hostname
        if not parent_host or parent_host.lower() not in page_hosts:
            raise ValueError(
                "Source artifact parent_url host is not in the registered official source hosts"
            )
        if not file_host or file_host.lower() not in attachment_hosts:
            raise ValueError(
                "Source artifact artifact_url host is not in the registered attachment hosts"
            )

    @staticmethod
    def _normalize_artifact_row(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("Source artifact row must be an object")
        sheet_name = str(value.get("sheet_name") or "").strip()
        if not sheet_name:
            raise ValueError("Source artifact row sheet_name is required")
        row_number = value.get("row_number")
        if isinstance(row_number, bool):
            raise ValueError("Source artifact row row_number must be a positive integer")
        try:
            row_number = int(row_number)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Source artifact row row_number must be a positive integer"
            ) from error
        if row_number < 1:
            raise ValueError("Source artifact row row_number must be a positive integer")
        cells_value = value.get("cells", {})
        if not isinstance(cells_value, dict):
            raise ValueError("Source artifact row cells must be an object")
        cells = {
            str(key).strip(): str(cell).strip()
            for key, cell in cells_value.items()
            if str(key).strip() and str(cell).strip()
        }
        row_kind = str(value.get("row_kind") or "tabular").strip()
        if row_kind not in {"tabular", "text_table", "tabular_parallel"}:
            raise ValueError(
                "Source artifact row row_kind must be tabular, tabular_parallel or text_table"
            )
        confidence = str(value.get("extraction_confidence") or "high").strip()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("Source artifact row extraction_confidence is unsupported")
        row_text = str(value.get("row_text") or "").strip()
        if not row_text:
            row_text = "；".join(f"{key}：{cell}" for key, cell in cells.items())
        row_key_suffix = str(value.get("row_key_suffix") or "").strip()
        if len(row_key_suffix) > 100:
            raise ValueError("Source artifact row row_key_suffix is too long")
        return {
            "sheet_name": sheet_name,
            "row_number": row_number,
            "row_kind": row_kind,
            "cells": cells,
            "row_text": row_text,
            "extraction_confidence": confidence,
            "row_key_suffix": row_key_suffix,
        }

    @staticmethod
    def _artifact_row_key(artifact_id: int, row: dict[str, Any]) -> str:
        identity = {
            "artifact_id": artifact_id,
            "sheet_name": row["sheet_name"],
            "row_number": row["row_number"],
            "row_kind": row["row_kind"],
            "row_key_suffix": row.get("row_key_suffix", ""),
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _artifact_candidate_key(artifact_row_id: int) -> str:
        return hashlib.sha256(
            f"artifact-job-candidate:{artifact_row_id}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _artifact_candidate_select_sql() -> str:
        return """
            SELECT candidates.*,
                   rows.artifact_id,
                   rows.sheet_name AS row_sheet_name,
                   rows.row_number AS row_number,
                   rows.row_kind AS row_kind,
                   rows.cells_json AS row_cells_json,
                   rows.row_text AS row_text,
                   rows.extraction_confidence AS extraction_confidence,
                   artifacts.artifact_url AS artifact_url,
                   artifacts.parent_url AS artifact_parent_url,
                   artifacts.content_sha256 AS artifact_content_sha256,
                   artifacts.storage_path AS artifact_storage_path,
                   artifacts.parser_version AS artifact_parser_version
            FROM artifact_job_candidates AS candidates
            JOIN source_artifact_rows AS rows ON rows.id = candidates.artifact_row_id
            JOIN source_artifacts AS artifacts ON artifacts.id = rows.artifact_id
        """

    @staticmethod
    def _select_artifact_job_candidate(
        connection: sqlite3.Connection,
        candidate_id: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            Database._artifact_candidate_select_sql()
            + " WHERE candidates.id = ?",
            (candidate_id,),
        ).fetchone()

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["config"] = json.loads(item.pop("config_json"))
        return item

    @staticmethod
    def _lead_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    @staticmethod
    def _artifact_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    @staticmethod
    def _source_artifact_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["cells"] = json.loads(item.pop("cells_json"))
        return item

    @staticmethod
    def _artifact_candidate_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["degree_levels"] = json.loads(item.pop("degree_levels_json"))
        item["major_tags"] = json.loads(item.pop("major_tags_json"))
        item["field_evidence"] = json.loads(item.pop("field_evidence_json"))
        if "row_cells_json" in item:
            item["row_cells"] = json.loads(item.pop("row_cells_json"))
        return item

    @staticmethod
    def _evidence_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    @staticmethod
    def _candidate_lead_event_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["degree_levels"] = json.loads(item.pop("degree_levels_json"))
        item["major_tags"] = json.loads(item.pop("major_tags_json"))
        item["field_evidence"] = json.loads(item.pop("field_evidence_json", "{}"))
        item["publication_basis"] = json.loads(
            item.pop("publication_basis_json", "{}")
        )
        return enrich_job(item)
