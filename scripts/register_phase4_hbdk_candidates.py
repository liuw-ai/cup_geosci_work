"""Register HBDK official-table rows as private, auditable candidates.

The official notice and table are readable, but the notice expresses the
deadline as seven working days and the project has not yet bound that phrase
to an approved Asia/Shanghai working-day calendar.  This script therefore
stops at ``official_content_verified`` and never creates a formal job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.db import Database


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json"
SOURCE_ID = "hbdk-official-recruitment"


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    capture = payload.get("capture") or {}
    raw = ROOT / str(capture.get("retained_path") or "")
    if not raw.is_file():
        raise ValueError("HBDK raw official table capture is missing")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    if digest != str(capture.get("sha256") or ""):
        raise ValueError("HBDK raw official table capture hash does not match the report")
    if payload.get("publication_decision", {}).get("formal_jobs_created") != 0:
        raise ValueError("HBDK evidence must not already claim formal jobs")
    deadline = payload.get("official_deadline") or {}
    if deadline.get("policy") != "asia_shanghai_weekdays_inclusive":
        raise ValueError("HBDK evidence must use the explicit Asia/Shanghai relative deadline policy")
    if not str(deadline.get("resolved_calendar_date") or "").strip():
        raise ValueError("HBDK evidence is missing the resolved calendar deadline")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("expected exactly two reviewed HBDK rows")
    return payload


def build_leads(payload: dict) -> list[dict]:
    source = payload["source"]
    capture = payload["capture"]
    deadline = payload["official_deadline"]
    leads: list[dict] = []
    for row in payload["rows"]:
        row_number = int(row["row_number"])
        leads.append(
            {
                "lead_provider": "华图教育",
                "lead_url": f"{source['discovery_page_url']}#official-row-{row_number}",
                "title": str(row["title"]).strip(),
                "employer_hint": "河北省地球物理勘查有限公司",
                "location_hint": row["location"],
                "province_hint": "河北",
                "published_date": deadline["published_date"],
                "official_url": source["official_notice_url"],
                "official_source_id": SOURCE_ID,
                "official_table_url": source["official_table_url"],
                "official_application_url": None,
                "third_party_table_url": None,
                "trust_grade": "B",
                "discovery_source_id": source["discovery_source_id"],
                "verification_note": (
                    "华图发现页已回溯至河北省地球物理勘查有限公司官方公告和官方岗位表；"
                    "已逐行读取岗位、专业、学历和地点。官方报名期限为相对工作日文本，"
                    "已按北京时间发布日期计第1个工作日的规则解析截止日；正式岗位发布仍需通过隔离数据库验证。"
                ),
                "metadata": {
                    "batch_id": "huatu-hbdk-2026-10-15",
                    "evidence_report": str(EVIDENCE.relative_to(ROOT)).replace("\\", "/"),
                    "discovery_page_url": source["discovery_page_url"],
                    "official_notice_url": source["official_notice_url"],
                    "official_table_url": source["official_table_url"],
                    "official_table_response_sha256": capture["sha256"],
                    "official_table_response_bytes": capture["bytes"],
                    "official_table_capture_path": capture["retained_path"],
                    "deadline_text": deadline["text"],
                    "deadline_policy": deadline["policy"],
                    "deadline_date": deadline["resolved_calendar_date"],
                    "deadline_working_days": deadline["working_days"],
                    "deadline_calendar_exceptions": deadline.get("calendar_exceptions", []),
                    "locator": f"official-row-{row_number}",
                    "row": row,
                    "publication_status": "candidate_only",
                },
            }
        )
    return leads


def register(database_path: Path, evidence_path: Path) -> list[dict]:
    payload = _load(evidence_path)
    leads = build_leads(payload)
    database = Database(database_path)
    database.initialize()
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    source = next((item for item in registry if item.get("id") == SOURCE_ID), None)
    if not isinstance(source, dict):
        raise ValueError(f"{SOURCE_ID} is missing from data/sources.json")
    database.upsert_source(source)
    stored_rows = []
    for lead in leads:
        row_number = int(lead["metadata"]["row"]["row_number"])
        existing = next(
            (
                item
                for item in database.list_candidate_leads(
                    discovery_source_id=lead["discovery_source_id"], limit=5000
                )
                if isinstance(item.get("metadata"), dict)
                and item["metadata"].get("batch_id") == lead["metadata"].get("batch_id")
                and int((item["metadata"].get("row") or {}).get("row_number") or -1) == row_number
            ),
            None,
        )
        stored = existing or database.create_candidate_lead(lead)
        if existing is not None:
            stored = database.update_candidate_lead(
                int(stored["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": SOURCE_ID,
                    "official_table_url": lead["official_table_url"],
                    "official_domain_status": "registered_source_match",
                    "verification_status": stored["verification_status"],
                    "conversion_status": "official_content_verified_not_student_published",
                    "verification_note": lead["verification_note"],
                    "metadata": lead["metadata"],
                    "trust_grade": "B",
                },
            )
        if stored["verification_status"] == "candidate":
            stored = database.update_candidate_lead(
                int(stored["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": SOURCE_ID,
                    "official_table_url": lead["official_table_url"],
                    "official_domain_status": "registered_source_match",
                    "verification_status": "official_url_found",
                    "verification_note": lead["verification_note"],
                    "metadata": lead["metadata"],
                    "trust_grade": "B",
                },
            )
        if stored["verification_status"] == "official_url_found":
            stored = database.update_candidate_lead(
                int(stored["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": SOURCE_ID,
                    "official_table_url": lead["official_table_url"],
                    "official_domain_status": "registered_source_match",
                    "verification_status": "official_content_verified",
                    "conversion_status": "official_content_verified_not_student_published",
                    "verification_note": lead["verification_note"],
                    "metadata": lead["metadata"],
                    "trust_grade": "B",
                },
            )
        if stored["verification_status"] != "official_content_verified":
            raise ValueError(f"unexpected HBDK candidate status: {stored['verification_status']}")
        stored_rows.append(stored)
    return stored_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    payload = _load(args.evidence)
    leads = build_leads(payload)
    if not args.apply:
        print(json.dumps({"mode": "dry_run", "candidate_count": len(leads)}, ensure_ascii=False, indent=2))
        return 0
    database_path = args.database.resolve()
    formal = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if database_path == formal and not args.allow_formal_database:
        raise SystemExit("refusing to write runtime/job_hub.sqlite3 without --allow-formal-database")
    stored = register(database_path, args.evidence)
    print(json.dumps({"candidate_count": len(stored), "candidate_ids": [int(item["id"]) for item in stored], "formal_jobs_created": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
