"""Register and publish the HBDK water-team evidence only in an isolated DB.

The official notice and linked Tencent documents are the source evidence.  The
two civil-construction rows are retained as rejected candidate audits; only
the rock-soil row is eligible for a local publication trial.  The formal
runtime database is refused unless an explicit override is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs/current/phase4-third-party-hbdk-water-official-evidence-2026-10-20.json"
SOURCE_ID = "hbdk-water-official-recruitment"
BATCH_ID = "official-hbdk-water-2026-10-20"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_evidence(path: Path = EVIDENCE) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("publication_decision", {}).get("formal_jobs_created_in_formal_database") != 0:
        raise ValueError("evidence report must not claim a formal database write")
    if payload.get("policy_checks", {}).get("formal_database_written") is not False:
        raise ValueError("evidence report must state formal_database_written=false")
    for capture in payload.get("captures", {}).values():
        if isinstance(capture, dict) and capture.get("retained_path"):
            raw = ROOT / str(capture["retained_path"])
            if not raw.is_file():
                raise ValueError(f"missing retained evidence: {raw}")
            if _sha256(raw) != str(capture.get("retained_sha256") or "").lower():
                raise ValueError(f"evidence hash mismatch: {raw}")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError("expected exactly three official HBDK water rows")
    return payload


def build_leads(payload: dict) -> list[dict]:
    source = payload["source"]
    facts = payload["notice_facts"]
    leads: list[dict] = []
    for row in payload["rows"]:
        row_number = int(row["row_number"])
        leads.append(
            {
                "lead_provider": "河北地矿集团官方公告",
                "lead_url": f"{source['official_notice_url']}#row-{row_number}",
                "title": row["title"],
                "employer_hint": source["employer"],
                "location_hint": "河北省沧州市新华区",
                "province_hint": "河北",
                "published_date": facts["published_date"],
                "official_url": source["official_notice_url"],
                "official_source_id": SOURCE_ID,
                "official_table_url": source["official_table_url"],
                "official_application_url": None,
                "third_party_table_url": None,
                "trust_grade": "A",
                "discovery_source_id": SOURCE_ID,
                "verification_note": (
                    f"官方公告、官方附件1岗位表和第{row_number}行已逐项绑定；"
                    "岗位表为官方公告链接的腾讯文档，报名方式为现场或邮箱，未伪造 HTTP 报名链接。"
                ),
                "metadata": {
                    "batch_id": BATCH_ID,
                    "evidence_report": str(EVIDENCE.relative_to(ROOT)).replace("\\", "/"),
                    "official_notice_url": source["official_notice_url"],
                    "official_table_url": source["official_table_url"],
                    "registration_form_url": source["official_registration_form_url"],
                    "official_table_sha256": payload["captures"]["official_position_table"]["retained_sha256"],
                    "registration_form_sha256": payload["captures"]["official_registration_form"]["retained_sha256"],
                    "deadline_text": facts["deadline_text"],
                    "deadline_date": facts["resolved_deadline_date"],
                    "deadline_policy": facts["deadline_policy"],
                    "row": row,
                    "locator": row["official_row_locator"],
                    "publication_status": "candidate_only",
                },
            }
        )
    return leads


def _source(database: Database) -> dict:
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    source = next((item for item in registry if item.get("id") == SOURCE_ID), None)
    if not isinstance(source, dict):
        raise ValueError(f"{SOURCE_ID} is missing from data/sources.json")
    database.upsert_source(source)
    return source


def _find_lead(database: Database, row_number: int) -> dict | None:
    for item in database.list_candidate_leads(discovery_source_id=SOURCE_ID, limit=5000):
        metadata = item.get("metadata") or {}
        row = metadata.get("row") or {}
        if metadata.get("batch_id") == BATCH_ID and int(row.get("row_number") or -1) == row_number:
            return item
    return None


def register(database_path: Path, evidence_path: Path = EVIDENCE) -> dict:
    payload = load_evidence(evidence_path)
    database = Database(database_path)
    database.initialize()
    _source(database)
    stored: list[dict] = []
    rejected: list[int] = []
    for lead in build_leads(payload):
        row_number = int(lead["metadata"]["row"]["row_number"])
        item = _find_lead(database, row_number) or database.create_candidate_lead(lead)
        if item["verification_status"] == "candidate":
            item = database.update_candidate_lead(
                int(item["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": SOURCE_ID,
                    "official_table_url": lead["official_table_url"],
                    "official_domain_status": "registered_source_match",
                    "verification_status": "official_url_found",
                    "verification_note": lead["verification_note"],
                    "metadata": lead["metadata"],
                    "trust_grade": "A",
                },
            )
        if item["verification_status"] == "official_url_found":
            item = database.update_candidate_lead(
                int(item["id"]),
                {
                    "official_url": lead["official_url"],
                    "official_source_id": SOURCE_ID,
                    "official_table_url": lead["official_table_url"],
                    "official_domain_status": "registered_source_match",
                    "verification_status": "official_content_verified",
                    "verification_note": lead["verification_note"],
                    "metadata": lead["metadata"],
                    "trust_grade": "A",
                },
            )
        if row_number in {1, 2} and item["verification_status"] == "official_content_verified":
            item = database.update_candidate_lead(
                int(item["id"]),
                {
                    "verification_status": "rejected",
                    "verification_note": (
                        "官方证据完整，但逐行专业门禁判定为完全不匹配："
                        + str(lead["metadata"]["row"]["major_requirement"])
                    ),
                },
            )
            rejected.append(row_number)
        stored.append(item)
    return {
        "candidate_ids": [int(item["id"]) for item in stored],
        "candidate_count": len(stored),
        "official_content_verified_count": sum(
            item["verification_status"] == "official_content_verified" for item in stored
        ),
        "rejected_major_mismatch_rows": rejected,
    }


def _settings(database_path: Path) -> Settings:
    import os

    os.environ["APP_DATABASE_PATH"] = str(database_path)
    os.environ["APP_DATA_DIR"] = str(database_path.parent)
    load_project_env(ROOT / ".env")
    return Settings.from_env()


def publish(database_path: Path, evidence_path: Path = EVIDENCE) -> dict:
    payload = load_evidence(evidence_path)
    database = Database(database_path)
    database.initialize()
    source = _source(database)
    settings = _settings(database_path)
    pipeline = JobPipeline(settings, database)
    lead = next(item for item in build_leads(payload) if int(item["metadata"]["row"]["row_number"]) == 3)
    stored = _find_lead(database, 3)
    if stored is None or stored["verification_status"] != "official_content_verified":
        raise ValueError("row 3 must be official_content_verified before publication")
    row = lead["metadata"]["row"]
    facts = payload["notice_facts"]
    raw = RawPosting(
        title=row["title"],
        employer=payload["source"]["employer"],
        source_url=payload["source"]["official_notice_url"],
        application_url=None,
        text=(
            f"官方岗位：{row['title']}；专业要求：{row['major_requirement']}；"
            f"学历要求：{row['degree_requirement']}；报名邮箱：sshdk@hbdkjt.cn。"
        ),
        summary="河北水文四队地质勘查有限公司官方招聘公告第3行，岩土方向。",
        published_date=facts["published_date"],
        deadline_date=facts["resolved_deadline_date"],
        location="河北省沧州市新华区",
        external_id=f"{BATCH_ID}-row-3",
        match_text=f"{row['title']} {row['major_requirement']} 岩土 勘查技术与工程",
        official_evidence_url=payload["source"]["official_notice_url"],
        field_evidence={
            "evidence_scope": "official_attachment_row",
            "岗位": row["title"],
            "官方公告链接": payload["source"]["official_notice_url"],
            "官方岗位表链接": payload["source"]["official_table_url"],
            "官方岗位表哈希": payload["captures"]["official_position_table"]["retained_sha256"],
            "官方岗位表行号": "3",
            "官方报名表链接": payload["source"]["official_registration_form_url"],
            "官方报名方式": facts["application_method"],
            "专业要求": row["major_requirement"],
            "学历要求": row["degree_requirement"],
            "工作地点": "河北省沧州市新华区",
            "官方截止日期": facts["resolved_deadline_date"],
        },
        qualification_text=f"专业要求：{row['major_requirement']}；学历要求：{row['degree_requirement']}",
    )
    job = pipeline.normalize_posting(raw, source)
    job.update(
        {
            "trust_grade": "A",
            "official_notice_url": payload["source"]["official_notice_url"],
            "official_table_url": payload["source"]["official_table_url"],
            "official_application_url": None,
            "table_sha256": payload["captures"]["official_position_table"]["retained_sha256"],
            "table_row_number": 3,
            "last_official_verified_at": payload["captured_at"],
            "no_deadline_expires_at": None,
        }
    )
    if job.get("publication_status") not in {"student_eligible", "unrestricted_eligible"}:
        raise ValueError(
            f"row 3 failed publication gate: {job.get('publication_status')} / {job.get('publication_basis')}"
        )
    job_id, outcome = database.save_job(job)
    database.mark_candidate_lead_published(int(stored["id"]), job_id)
    return {
        "job_id": job_id,
        "outcome": outcome,
        "publication_status": job.get("publication_status"),
        "publication_layer": database.find_job(job_id, student_visible=False).get("publication_layer"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    path = args.database.resolve()
    formal = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if args.apply and path == formal and not args.allow_formal_database:
        raise SystemExit("refusing to write runtime/job_hub.sqlite3; use an isolated copy")
    if not args.apply:
        payload = load_evidence()
        print(json.dumps({"mode": "dry_run", "rows": len(build_leads(payload))}, ensure_ascii=False, indent=2))
        return 0
    result = register(path)
    if args.publish:
        result["publication"] = publish(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
