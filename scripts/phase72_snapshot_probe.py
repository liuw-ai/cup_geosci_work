"""Read-only Phase 7.2 snapshot probe.

This report keeps the imported server database immutable and exposes the
specific government and third-party records that can be reviewed for a real
publication batch.  It intentionally does not initialize the application
schema or change any row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def _rows(connection: sqlite3.Connection, query: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(query, values).fetchall()]


def build_report(database_path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(database_path.read_bytes()).hexdigest()
    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        tables = {
            name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in (
                "jobs",
                "candidate_leads",
                "artifact_job_candidates",
                "source_artifacts",
                "sources",
                "job_evidence",
            )
        }
        job_sources = _rows(
            connection,
            """
            SELECT source_id, source_name, publication_status, publication_layer,
                   status, COUNT(*) AS count
            FROM jobs
            GROUP BY source_id, source_name, publication_status,
                     publication_layer, status
            ORDER BY count DESC, source_id
            """,
        )
        artifact_candidates = _rows(
            connection,
            """
            SELECT candidates.id, artifact_rows.artifact_id, candidates.source_id,
                   candidates.title, candidates.employer, candidates.location,
                   deadline_date, review_status, official_page_url,
                   artifact_rows.sheet_name AS row_sheet_name,
                   artifact_rows.row_number, artifact_rows.row_text,
                   candidates.field_evidence_json, candidates.review_note,
                   candidates.updated_at
            FROM artifact_job_candidates AS candidates
            LEFT JOIN source_artifact_rows AS artifact_rows
              ON artifact_rows.id = candidates.artifact_row_id
            WHERE candidates.review_status IN ('official_content_verified', 'needs_review', 'published')
            ORDER BY candidates.review_status, candidates.id
            """,
        )
        leads = _rows(
            connection,
            """
            SELECT id, lead_provider, discovery_source_id, title, employer_hint,
                   location_hint, verification_status, official_url,
                   official_source_id, official_domain_status, metadata_json,
                   verification_note, updated_at
            FROM candidate_leads
            WHERE verification_status NOT IN ('rejected', 'expired')
            ORDER BY verification_status, id
            """,
        )
        return {
            "report": "phase72_snapshot_probe",
            "database": str(database_path),
            "sha256": digest,
            "integrity_check": integrity,
            "tables": tables,
            "job_source_status_breakdown": job_sources,
            "reviewable_artifact_candidates": artifact_candidates,
            "reviewable_candidate_leads": leads,
            "read_only": True,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "database": report["database"],
        "sha256": report["sha256"],
        "integrity_check": report["integrity_check"],
        "tables": report["tables"],
        "reviewable_artifact_candidates": len(report["reviewable_artifact_candidates"]),
        "reviewable_candidate_leads": len(report["reviewable_candidate_leads"]),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
