"""Build a read-only audit of formal jobs attributed to discovery channels."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "runtime" / "job_hub.sqlite3"
OUTPUT = ROOT / "docs" / "current" / "phase4-third-party-formal-job-audit-2026-10-21.json"


def build_report(database_path: Path = DATABASE) -> dict[str, Any]:
    """Return a deterministic, read-only formal third-party job audit."""

    database_updated_at = datetime.fromtimestamp(
        database_path.stat().st_mtime, tz=timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                id, title, employer, status, publication_layer,
                third_party_source_id, third_party_discovery_url,
                third_party_batch_id, third_party_discovery_sha256,
                third_party_table_url, official_notice_url,
                official_table_url, official_application_url, application_url,
                deadline_date, no_deadline_expires_at,
                last_official_verified_at
            FROM jobs
            WHERE third_party_source_id IS NOT NULL
              AND TRIM(third_party_source_id) <> ''
            ORDER BY id
            """
        ).fetchall()

    items: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        official_evidence = {
            key: item.get(key)
            for key in ("official_notice_url", "official_table_url", "official_application_url")
            if item.get(key)
        }
        discovery_urls = {
            value
            for value in (item.get("third_party_discovery_url"), item.get("third_party_table_url"))
            if value
        }
        application_url = item.get("application_url") or item.get("official_application_url")
        checks = {
            "official_evidence_bound": bool(official_evidence),
            "third_party_url_not_application": not (application_url and application_url in discovery_urls),
        }
        item["official_evidence"] = official_evidence
        item["checks"] = checks
        if not all(checks.values()):
            violations.append(
                {
                    "job_id": item["id"],
                    "title": item["title"],
                    "failed_checks": [name for name, passed in checks.items() if not passed],
                }
            )
        items.append(item)

    source_counts: dict[str, int] = {}
    for item in items:
        source_id = str(item["third_party_source_id"])
        source_counts[source_id] = source_counts.get(source_id, 0) + 1

    return {
        "phase": "phase4",
        "generated_at": database_updated_at,
        "generated_from_database_mtime": True,
        "status": "pass" if not violations else "fail",
        "policy": {
            "read_only": True,
            "third_party_links_are_internal_evidence": True,
            "official_evidence_required_before_publication": True,
            "official_application_url_may_be_null_when_notice_defines_email_or_on_site_method": True,
        },
        "database": {
            "path": "runtime/job_hub.sqlite3",
            "formal_third_party_job_count": len(items),
        },
        "source_counts": dict(sorted(source_counts.items())),
        "jobs": items,
        "violations": violations,
    }


def main() -> int:
    report = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "status": report["status"], "count": report["database"]["formal_third_party_job_count"], "violations": len(report["violations"])}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
