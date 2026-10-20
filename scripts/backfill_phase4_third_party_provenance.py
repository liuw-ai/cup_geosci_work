"""Backfill internal third-party discovery provenance onto published jobs.

The public job links remain official links.  This script only copies the
discovery source/page/batch evidence from the linked candidate lead into
dedicated internal job columns so later exports do not depend on parsing
metadata_json or retaining an implicit join.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from job_hub.db import Database  # noqa: E402
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _provenance(row: sqlite3.Row) -> dict[str, str | None]:
    metadata = json.loads(row["lead_metadata_json"] or "{}")
    evidence = json.loads(row["job_field_evidence_json"] or "{}")
    source_id = _text(row["discovery_source_id"])
    discovery_url = _text(metadata.get("discovery_page_url"))
    if not discovery_url:
        discovery_url = _text(evidence.get("third_party_discovery_url"))
    if not discovery_url:
        discovery_url = _text(row["lead_url"])
    batch_id = _text(metadata.get("batch_id"))
    discovery_sha256 = _text(metadata.get("discovery_page_sha256"))
    if discovery_sha256 and not SHA256_RE.fullmatch(discovery_sha256):
        discovery_sha256 = None
    return {
        "third_party_source_id": source_id,
        "third_party_discovery_url": discovery_url,
        "third_party_batch_id": batch_id,
        "third_party_discovery_sha256": discovery_sha256,
    }


def collect(database_path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                jobs.id AS job_id,
                jobs.third_party_source_id AS old_source_id,
                jobs.third_party_discovery_url AS old_discovery_url,
                jobs.third_party_batch_id AS old_batch_id,
                jobs.third_party_discovery_sha256 AS old_discovery_sha256,
                jobs.field_evidence_json AS job_field_evidence_json,
                candidate_leads.discovery_source_id,
                candidate_leads.lead_url,
                candidate_leads.metadata_json AS lead_metadata_json
            FROM candidate_leads
            JOIN jobs ON jobs.id = candidate_leads.published_job_id
            WHERE candidate_leads.published_job_id IS NOT NULL
            ORDER BY jobs.id, candidate_leads.id
            """
        ).fetchall()
        seen: set[int] = set()
        result: list[dict[str, Any]] = []
        for row in rows:
            job_id = int(row["job_id"])
            if job_id in seen:
                continue
            seen.add(job_id)
            next_values = _provenance(row)
            result.append(
                {
                    "job_id": job_id,
                    "before": {
                        "third_party_source_id": row["old_source_id"],
                        "third_party_discovery_url": row["old_discovery_url"],
                        "third_party_batch_id": row["old_batch_id"],
                        "third_party_discovery_sha256": row["old_discovery_sha256"],
                    },
                    "after": next_values,
                }
            )
        return result
    finally:
        connection.close()


def apply(database_path: Path, rows: list[dict[str, Any]]) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executemany(
            """
            UPDATE jobs
            SET third_party_source_id = ?,
                third_party_discovery_url = ?,
                third_party_batch_id = ?,
                third_party_discovery_sha256 = ?
            WHERE id = ?
            """,
            [
                (
                    item["after"]["third_party_source_id"],
                    item["after"]["third_party_discovery_url"],
                    item["after"]["third_party_batch_id"],
                    item["after"]["third_party_discovery_sha256"],
                    item["job_id"],
                )
                for item in rows
            ],
        )
        connection.commit()
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    database_path = args.database.resolve()
    formal_path = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if args.apply and database_path == formal_path and not args.allow_formal_database:
        raise SystemExit(
            "refusing to write runtime/job_hub.sqlite3 without --allow-formal-database"
        )
    Database(database_path).initialize()
    rows = collect(database_path)
    if args.apply:
        apply(database_path, rows)
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
