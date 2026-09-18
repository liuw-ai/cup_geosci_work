from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

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
"""


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def upsert_source(self, source: dict[str, Any]) -> None:
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
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE crawl_runs
                SET finished_at = ?, status = ?, discovered_count = ?,
                    inserted_count = ?, updated_count = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    utc_now(),
                    status,
                    discovered_count,
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
                        source_url, application_url, location, published_date,
                        deadline_date, degree_levels_json, major_tags_json, summary,
                        description, relevance_score, relevance_band, status,
                        first_seen_at, last_seen_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                return job_id, "created"

            job_id = int(existing["id"])
            if existing["content_hash"] == job["content_hash"]:
                connection.execute(
                    """
                    UPDATE jobs
                    SET external_id = ?, title = ?, employer = ?, group_name = ?,
                        category = ?, source_tier = ?, source_name = ?,
                        source_url = ?, application_url = ?, location = ?,
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
                return job_id, "unchanged"

            connection.execute(
                """
                UPDATE jobs
                SET external_id = ?, content_hash = ?, title = ?, employer = ?,
                    group_name = ?, category = ?, source_tier = ?, source_name = ?,
                    source_url = ?, application_url = ?, location = ?,
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
            return job_id, "updated"

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

    def list_jobs(
        self,
        *,
        category: str | None = None,
        degree: str | None = None,
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
        if relevance_band:
            clauses.append("relevance_band = ?")
            values.append(relevance_band)
        if q:
            clauses.append(
                "(title LIKE ? OR employer LIKE ? OR location LIKE ? OR description LIKE ?)"
            )
            search = f"%{q.strip()}%"
            values.extend([search, search, search, search])
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
    def _source_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["config"] = json.loads(item.pop("config_json"))
        return item

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["degree_levels"] = json.loads(item.pop("degree_levels_json"))
        item["major_tags"] = json.loads(item.pop("major_tags_json"))
        return enrich_job(item)
