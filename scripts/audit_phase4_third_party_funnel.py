"""Audit the third-party discovery-to-publication funnel without writing data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "runtime" / "job_hub.sqlite3"
OUTPUT = ROOT / "docs" / "current" / "phase4-third-party-funnel-audit-2026-10-18.json"
PUBLICATION_LAYERS = {
    "exact_match",
    "broad_geoscience_match",
    "incomplete_but_basic_match",
    "source_unavailable",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_database(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    with sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        leads = connection.execute(
            """
            SELECT candidate_leads.*, jobs.status AS job_status,
                   jobs.publication_layer, jobs.publication_status,
                   jobs.official_notice_url AS job_official_notice_url,
                   jobs.official_table_url AS job_official_table_url,
                   jobs.official_application_url AS job_official_application_url
            FROM candidate_leads
            LEFT JOIN jobs ON jobs.id = candidate_leads.published_job_id
            ORDER BY candidate_leads.id
            """
        ).fetchall()

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for lead in leads:
        item = dict(lead)
        published = item.get("verification_status") == "published" or item.get("published_job_id") is not None
        lead_issues: list[str] = []
        if published:
            if not item.get("published_job_id"):
                lead_issues.append("published_lead_missing_published_job_id")
            if item.get("conversion_status") != "official_content_verified":
                lead_issues.append("published_lead_not_officially_verified")
            if not item.get("job_status"):
                lead_issues.append("published_job_not_found")
            if item.get("publication_layer") not in PUBLICATION_LAYERS:
                lead_issues.append("published_job_not_in_student_layer")
            official_evidence = any(
                item.get(field)
                for field in (
                    "official_url",
                    "official_table_url",
                    "job_official_notice_url",
                    "job_official_table_url",
                )
            )
            if not official_evidence:
                lead_issues.append("published_lead_missing_official_evidence_url")
            application_url = item.get("job_official_application_url") or item.get(
                "official_application_url"
            )
            if application_url and not str(application_url).startswith(("http://", "https://")):
                lead_issues.append("official_application_url_not_http")
        item["audit_status"] = "pass" if not lead_issues else "fail"
        item["issues"] = lead_issues
        rows.append(item)
        if lead_issues:
            issues.append({"lead_id": item.get("id"), "issues": lead_issues})

    published_count = sum(1 for item in rows if item["verification_status"] == "published")
    unresolved_count = sum(1 for item in rows if item["verification_status"] != "published")
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": "phase4",
        "database": {
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256(path),
            "integrity_check": integrity,
        },
        "policy": {
            "published_lead_requires_official_evidence": True,
            "published_job_requires_student_layer": True,
            "third_party_url_is_not_application_url": True,
        },
        "summary": {
            "candidate_leads": len(rows),
            "published_leads": published_count,
            "unresolved_or_pending_leads": unresolved_count,
            "failed_leads": len(issues),
            "audit_status": "pass" if integrity == "ok" and not issues else "fail",
        },
        "issues": issues,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = audit_database(args.database)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["audit_status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
