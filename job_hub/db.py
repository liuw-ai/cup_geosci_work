from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from job_hub.contracts import (
    OFFICIAL_EVIDENCE_TYPES,
    is_http_url,
    validate_artifact_candidate,
    validate_artifact_candidate_transition,
    validate_candidate_lead_transition,
    validate_job_evidence,
    validate_source_artifact,
    validate_source_record,
)
from job_hub.employers import enrich_job
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
    city TEXT,
    country_or_region TEXT,
    location_confidence TEXT NOT NULL DEFAULT 'unknown',
    location_evidence TEXT,
    verification_status TEXT NOT NULL DEFAULT 'published_official',
    official_evidence_url TEXT,
    published_date TEXT,
    deadline_date TEXT,
    degree_levels_json TEXT NOT NULL DEFAULT '[]',
    major_tags_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    relevance_score INTEGER NOT NULL DEFAULT 0,
    relevance_band TEXT NOT NULL DEFAULT '拓展机会',
    status TEXT NOT NULL DEFAULT 'open',
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
    error_message TEXT
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
"""


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


_ACTIVE_TRANSACTION: ContextVar[tuple[Path, sqlite3.Connection] | None] = ContextVar(
    "job_hub_active_transaction", default=None
)


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_schema(connection)

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
            "city": "TEXT",
            "country_or_region": "TEXT",
            "location_confidence": "TEXT NOT NULL DEFAULT 'unknown'",
            "location_evidence": "TEXT",
            "verification_status": "TEXT NOT NULL DEFAULT 'published_official'",
            "official_evidence_url": "TEXT",
        }
        added_official_evidence_url = False
        for name, definition in additions.items():
            if name not in existing_columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
                added_official_evidence_url = (
                    added_official_evidence_url or name == "official_evidence_url"
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
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_province ON jobs(province, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_canonical_employer "
            "ON jobs(canonical_employer_id, status)"
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
            connection.execute(
                """
                INSERT INTO sources (
                    id, name, publisher, homepage_url, source_type, category,
                    source_tier, enabled, config_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    publisher=excluded.publisher,
                    homepage_url=excluded.homepage_url,
                    source_type=excluded.source_type,
                    category=excluded.category,
                    source_tier=excluded.source_tier,
                    enabled=excluded.enabled,
                    config_json=excluded.config_json,
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
                    int(source.get("enabled", True)),
                    payload,
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

    def get_source_health(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_health WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_source_health(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_health ORDER BY checked_at DESC, source_id"
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

    def record_crawl_start(self, source_id: str) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crawl_runs (source_id, started_at, status)
                VALUES (?, ?, 'running')
                """,
                (source_id, utc_now()),
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
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE crawl_runs
                SET finished_at = ?, status = ?, discovered_count = ?,
                    open_matching_count = ?, inserted_count = ?,
                    updated_count = ?, error_message = ?
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

    def save_job(self, job: dict[str, Any]) -> tuple[int, str]:
        now = utc_now()
        json_fields = {
            "degree_levels_json": json.dumps(
                job.get("degree_levels", []), ensure_ascii=False
            ),
            "major_tags_json": json.dumps(job.get("major_tags", []), ensure_ascii=False),
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
                        parent_employer_name, province, city, country_or_region,
                        location_confidence, location_evidence, verification_status,
                        official_evidence_url, published_date,
                        deadline_date, degree_levels_json, major_tags_json, summary,
                        description, relevance_score, relevance_band, status,
                        first_seen_at, last_seen_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        job.get("city"),
                        job.get("country_or_region"),
                        job.get("location_confidence", "unknown"),
                        job.get("location_evidence"),
                        job.get("verification_status", "published_official"),
                        job.get("official_evidence_url", job["source_url"]),
                        job.get("published_date"),
                        job.get("deadline_date"),
                        json_fields["degree_levels_json"],
                        json_fields["major_tags_json"],
                        job.get("summary", ""),
                        job.get("description", ""),
                        job["relevance_score"],
                        job["relevance_band"],
                        job.get("status", "open"),
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
                        parent_employer_name = ?, province = ?, city = ?,
                        country_or_region = ?, location_confidence = ?,
                        location_evidence = ?, verification_status = ?,
                        official_evidence_url = ?,
                        published_date = ?, deadline_date = ?, degree_levels_json = ?,
                        major_tags_json = ?, summary = ?, description = ?,
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
                        job.get("city"),
                        job.get("country_or_region"),
                        job.get("location_confidence", "unknown"),
                        job.get("location_evidence"),
                        job.get("verification_status", "published_official"),
                        job.get("official_evidence_url", job["source_url"]),
                        job.get("published_date"),
                        job.get("deadline_date"),
                        json_fields["degree_levels_json"],
                        json_fields["major_tags_json"],
                        job.get("summary", ""),
                        job.get("description", ""),
                        job["relevance_score"],
                        job["relevance_band"],
                        job.get("status", "open"),
                        now,
                        job_id,
                    ),
                )
                self._ensure_official_page_evidence(connection, job_id, job)
                return job_id, "unchanged"

            connection.execute(
                """
                UPDATE jobs
                SET external_id = ?, content_hash = ?, title = ?, employer = ?,
                    group_name = ?, category = ?, source_tier = ?, source_name = ?,
                    source_url = ?, application_url = ?, location = ?,
                    canonical_employer_id = ?, canonical_employer_name = ?,
                    parent_employer_name = ?, province = ?, city = ?,
                    country_or_region = ?, location_confidence = ?,
                    location_evidence = ?, verification_status = ?,
                    official_evidence_url = ?,
                    published_date = ?, deadline_date = ?, degree_levels_json = ?,
                    major_tags_json = ?, summary = ?, description = ?,
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
                    job.get("city"),
                    job.get("country_or_region"),
                    job.get("location_confidence", "unknown"),
                    job.get("location_evidence"),
                    job.get("verification_status", "published_official"),
                    job.get("official_evidence_url", job["source_url"]),
                    job.get("published_date"),
                    job.get("deadline_date"),
                    json_fields["degree_levels_json"],
                    json_fields["major_tags_json"],
                    job.get("summary", ""),
                    job.get("description", ""),
                    job["relevance_score"],
                    job["relevance_band"],
                    job.get("status", "open"),
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
            return job_id, "updated"

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
                    extraction_status=excluded.extraction_status,
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
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM source_artifacts"
        values: list[Any] = []
        if source_id:
            query += " WHERE source_id = ?"
            values.append(source_id)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        values.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._artifact_row(row) for row in rows]

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
                connection.execute(
                    """
                    INSERT INTO source_artifact_rows (
                        artifact_id, row_key, sheet_name, row_number, row_kind,
                        cells_json, row_text, extraction_confidence, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(artifact_id, row_key) DO UPDATE SET
                        cells_json = excluded.cells_json,
                        row_text = excluded.row_text,
                        extraction_confidence = excluded.extraction_confidence,
                        updated_at = excluded.updated_at
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
        editable = {"review_status", "review_note"}
        unexpected = set(changes) - editable
        if unexpected:
            raise ValueError(
                "Artifact candidate fields are not editable after extraction: "
                + ", ".join(sorted(unexpected))
            )
        review_note = self._optional_text(
            changes.get("review_note", current.get("review_note"))
        ) or ""
        review_status = validate_artifact_candidate_transition(
            current["review_status"],
            changes.get("review_status", current["review_status"]),
            review_note=review_note,
        )
        if review_status == "published":
            raise ValueError(
                "Use mark_artifact_job_candidate_published after saving the verified job"
            )
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE artifact_job_candidates
                SET review_status = ?, review_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (review_status, review_note, now, candidate_id),
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

    def find_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job_row(row) if row else None

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
                       relevance_score, relevance_band, status
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
            }
            if all(current[key] == value for key, value in derived.items()):
                return False
            connection.execute(
                """
                UPDATE jobs
                SET category = ?, degree_levels_json = ?, major_tags_json = ?,
                    relevance_score = ?, relevance_band = ?, status = ?
                WHERE id = ?
                """,
                (
                    category,
                    degrees_json,
                    majors_json,
                    relevance_score,
                    relevance_band,
                    status,
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

    def update_job_normalization(
        self,
        job_id: int,
        *,
        canonical_employer_id: str | None,
        canonical_employer_name: str | None,
        parent_employer_name: str | None,
        location: str | None = None,
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
                       parent_employer_name, province, city, country_or_region,
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
                        province = ?, city = ?, country_or_region = ?,
                        location_confidence = ?, location_evidence = ?
                    WHERE id = ?
                    """,
                    (
                        values["location"],
                        values["canonical_employer_id"],
                        values["canonical_employer_name"],
                        values["parent_employer_name"],
                        values["province"],
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
                    parent_employer_name = ?, province = ?, city = ?,
                    country_or_region = ?, location_confidence = ?,
                    location_evidence = ?
                WHERE id = ?
                """,
                (
                    values["canonical_employer_id"],
                    values["canonical_employer_name"],
                    values["parent_employer_name"],
                    values["province"],
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
        return True

    def list_jobs(
        self,
        *,
        category: str | None = None,
        degree: str | None = None,
        province: str | None = None,
        relevance_band: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int | None = 20,
        only_open: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        values: list[Any] = []
        if only_open:
            clauses.append("status = 'open'")
        if category:
            clauses.append("category = ?")
            values.append(category)
        if degree:
            clauses.append("degree_levels_json LIKE ?")
            values.append(f'%"{degree}"%')
        if province:
            clauses.append("province = ?")
            values.append(province)
        if relevance_band:
            clauses.append("relevance_band = ?")
            values.append(relevance_band)
        if q:
            clauses.append(
                "(title LIKE ? OR employer LIKE ? OR canonical_employer_name LIKE ? "
                "OR location LIKE ? OR description LIKE ?)"
            )
            search = f"%{q.strip()}%"
            values.extend([search, search, search, search, search])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            count = connection.execute(
                f"SELECT COUNT(*) FROM jobs{where}", values
            ).fetchone()[0]
            query = f"""
                SELECT * FROM jobs{where}
                ORDER BY
                    CASE relevance_band
                        WHEN '强相关' THEN 1
                        WHEN '相关机会' THEN 2
                        ELSE 3
                    END,
                    CASE WHEN deadline_date IS NULL THEN 1 ELSE 0 END,
                    deadline_date ASC,
                    updated_at DESC
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

    def list_categories(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM jobs
                WHERE status = 'open'
                GROUP BY category
                ORDER BY count DESC, category
                """
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
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT jobs.*, job_events.event_type, job_events.occurred_at
                FROM job_events
                JOIN jobs ON jobs.id = job_events.job_id
                WHERE job_events.occurred_at >= ?
                  AND job_events.occurred_at < ?
                  AND jobs.status = 'open'
                ORDER BY jobs.relevance_score DESC, jobs.deadline_date ASC
                """,
                (start_value, end_value),
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
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'open'
                  AND deadline_date IS NOT NULL
                  AND deadline_date >= ?
                  AND deadline_date <= ?
                ORDER BY deadline_date ASC, relevance_score DESC
                """,
                (start_date, end_date),
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def count_open_jobs(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'open'"
                ).fetchone()[0]
            )

    def record_service_heartbeat(
        self,
        service_name: str,
        status: str,
        detail: str = "",
    ) -> None:
        """Persist a liveness signal for a long-running service such as worker."""
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
        now = utc_now()
        metadata = lead.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Candidate lead metadata must be an object")
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO candidate_leads (
                    lead_provider, lead_url, title, employer_hint, location_hint,
                    province_hint, published_date, official_url, verification_status,
                    verification_note, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
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
            row = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        return self._lead_row(row)

    def get_candidate_lead(self, lead_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        return self._lead_row(row) if row else None

    def list_candidate_leads(
        self,
        verification_status: str | None = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM candidate_leads"
        values: list[Any] = []
        if verification_status:
            query += " WHERE verification_status = ?"
            values.append(verification_status)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        values.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._lead_row(row) for row in rows]

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
            "verification_note",
            "metadata",
            "verification_status",
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
            connection.execute(
                """
                UPDATE candidate_leads
                SET employer_hint = ?, location_hint = ?, province_hint = ?,
                    published_date = ?, official_url = ?, verification_status = ?,
                    verification_note = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    fields["employer_hint"],
                    fields["location_hint"],
                    fields["province_hint"],
                    fields["published_date"],
                    fields["official_url"],
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
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'expired', updated_at = ?
                WHERE status = 'open'
                  AND deadline_date IS NOT NULL
                  AND deadline_date < ?
                """,
                (now, today),
            )
            return int(cursor.rowcount)

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
        if row_kind not in {"tabular", "text_table"}:
            raise ValueError("Source artifact row row_kind must be tabular or text_table")
        confidence = str(value.get("extraction_confidence") or "high").strip()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("Source artifact row extraction_confidence is unsupported")
        row_text = str(value.get("row_text") or "").strip()
        if not row_text:
            row_text = "；".join(f"{key}：{cell}" for key, cell in cells.items())
        return {
            "sheet_name": sheet_name,
            "row_number": row_number,
            "row_kind": row_kind,
            "cells": cells,
            "row_text": row_text,
            "extraction_confidence": confidence,
        }

    @staticmethod
    def _artifact_row_key(artifact_id: int, row: dict[str, Any]) -> str:
        identity = {
            "artifact_id": artifact_id,
            "sheet_name": row["sheet_name"],
            "row_number": row["row_number"],
            "row_kind": row["row_kind"],
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
        return enrich_job(item)
