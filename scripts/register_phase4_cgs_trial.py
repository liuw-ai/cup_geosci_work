"""Register a recovered Huatu-to-CGS attachment as private, auditable leads.

This is deliberately separate from the generic position-table importer.  The
CGS file is an official DOCX research-plan table, not a civil-service table:
it has no position code or legal employer column.  The script therefore keeps
the original row evidence and never invents missing fields or publishes rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.db import Database


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_PATH = PROJECT_ROOT / "docs/current/phase4-third-party-cgs-2026-10-18.json"


def _load_evidence(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("trial_status") not in {
        "official_table_recovered_private_review",
        "official_table_recovered_and_locally_published",
    }:
        raise ValueError("unexpected CGS trial status")
    recovery = payload.get("official_recovery") or {}
    discovery = payload.get("discovery") or {}
    required = {
        "official_table_url": recovery.get("official_table_url"),
        "official_table_sha256": recovery.get("official_table_sha256"),
        "discovery_page_url": discovery.get("discovery_page_url"),
    }
    if any(not str(value or "").strip() for value in required.values()):
        raise ValueError("CGS evidence is missing a required URL or SHA-256")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("CGS evidence must contain exactly four reviewed rows")
    return payload


def build_leads(payload: dict) -> list[dict]:
    discovery = payload["discovery"]
    recovery = payload["official_recovery"]
    leads: list[dict] = []
    for row in payload["rows"]:
        row_number = int(row["row_number"])
        lead_url = f"{discovery['discovery_page_url']}#official-row-{row_number}"
        leads.append(
            {
                "lead_provider": "华图教育",
                "lead_url": lead_url,
                "title": str(row["title"]).strip(),
                "employer_hint": recovery["employer"],
                "location_hint": recovery["location"],
                "province_hint": "北京",
                "published_date": recovery["published_date"],
                "official_url": recovery["official_table_url"],
                "official_source_id": recovery["source_id"],
                "official_table_url": recovery["official_table_url"],
                "third_party_table_url": None,
                "official_application_url": None,
                "trust_grade": "B",
                "discovery_source_id": discovery["source_id"],
                "verification_note": (
                    "华图页面发现后回溯到中国地质调查局自然资源综合调查指挥中心"
                    "官方博士后计划附件；已逐行核对。该行未直接出现四个核心毕业专业原名，"
                    "保持 official_content_verified 私有审核状态，不自动发布。"
                ),
                "metadata": {
                    "batch_id": "huatu-cgs-postdoc-2026-09-30",
                    "locator": f"official-row-{row_number}",
                    "source_grade": "B",
                    "discovery_page_url": discovery["discovery_page_url"],
                    "discovery_page_sha256": discovery["discovery_page_sha256"],
                    "official_table_url": recovery["official_table_url"],
                    "official_table_sha256": recovery["official_table_sha256"],
                    "official_table_bytes": recovery["official_table_bytes"],
                    "official_table_capture_path": recovery["official_table_capture_path"],
                    "official_application_method": recovery["official_application_method"],
                    "deadline_policy": recovery["deadline_policy"],
                    "no_deadline_expires_at": recovery["no_deadline_expires_at"],
                    "row": row,
                    "title": row["title"],
                    "employer": recovery["employer"],
                    "published_date": recovery["published_date"],
                    "location": recovery["location"],
                    "qualification_text": (
                        f"专业要求：{row['major_requirement']}；"
                        f"学历要求：{row['degree_requirement']}"
                    ),
                    "match_text": (
                        f"{row['title']} {recovery['employer']} "
                        f"{row['major_requirement']} {recovery['location']}"
                    ),
                    "field_evidence": {
                        "evidence_scope": "official_attachment_row",
                        "岗位": row["title"],
                        "用人单位": recovery["employer"],
                        "专业要求": row["major_requirement"],
                        "学历要求": row["degree_requirement"],
                        "工作地点": recovery["location"],
                        "招聘人数": str(row["headcount"]),
                        "指导教师": row.get("supervisor") or "",
                        "官方发布日期": recovery["published_date"],
                        "官方岗位表链接": recovery["official_table_url"],
                        "官方岗位表哈希": recovery["official_table_sha256"],
                        "官方岗位表行号": str(row_number),
                        "官方报名方式": recovery["official_application_method"],
                        "官方未公布截止日期": recovery["no_deadline_expires_at"],
                    },
                    "publication_status": "candidate_only",
                    "student_publication": "broad_geoscience_reviewed",
                },
            }
        )
    return leads


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()

    payload = _load_evidence(args.evidence)
    leads = build_leads(payload)
    if not args.apply:
        print(json.dumps({"candidate_count": len(leads), "candidates": leads}, ensure_ascii=False, indent=2))
        return

    database_path = args.database.resolve()
    formal_path = (PROJECT_ROOT / "runtime/job_hub.sqlite3").resolve()
    if database_path == formal_path and not args.allow_formal_database:
        raise SystemExit(
            "refusing to write runtime/job_hub.sqlite3 without --allow-formal-database"
        )

    database = Database(database_path)
    database.initialize()
    # Keep the persisted source contract in sync with the reviewed registry.
    # This is required before URL assessment; otherwise a newly approved
    # official attachment host is incorrectly treated as a domain mismatch.
    registry = json.loads(
        (PROJECT_ROOT / "data/sources.json").read_text(encoding="utf-8")
    )
    cgs_source = next(
        (item for item in registry if item.get("id") == "cgs-notices"), None
    )
    if not isinstance(cgs_source, dict):
        raise ValueError("cgs-notices is missing from data/sources.json")
    database.upsert_source(cgs_source)
    created = []
    for lead in leads:
        stored = database.create_candidate_lead(lead)
        if stored["verification_status"] == "candidate":
            stored = database.update_candidate_lead(
                int(stored["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": lead["official_source_id"],
                    "official_table_url": lead["official_table_url"],
                    "official_domain_status": "registered_source_match",
                    "verification_status": "official_url_found",
                    "verification_note": "官方附件 URL 已绑定至登记的 cgs-notices 来源。",
                    "metadata": lead["metadata"],
                    "trust_grade": "B",
                },
            )
        if stored["verification_status"] == "official_url_found":
            stored = database.update_candidate_lead(
                int(stored["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": lead["official_source_id"],
                    "official_table_url": lead["official_table_url"],
                    "third_party_table_url": None,
                    "official_application_url": None,
                    "official_domain_status": "registered_source_match",
                    "verification_status": "official_content_verified",
                    "conversion_status": "official_content_verified_not_student_published",
                    "verification_note": lead["verification_note"],
                    "metadata": lead["metadata"],
                    "trust_grade": "B",
                },
            )
        if stored["verification_status"] != "official_content_verified":
            raise ValueError(
                f"unexpected CGS candidate status: {stored['verification_status']}"
            )
        created.append(stored)
    print(
        json.dumps(
            {
                "candidate_count": len(created),
                "candidate_ids": [int(item["id"]) for item in created],
                "verification_statuses": [item["verification_status"] for item in created],
                "formal_jobs_created": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
