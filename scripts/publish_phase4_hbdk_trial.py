"""Promote the two verified HBDK rows after resolving the official deadline.

The script is intentionally separate from candidate registration. It only
accepts the retained official notice/table evidence, requires the explicit
Asia/Shanghai working-day deadline record, and refuses the formal runtime DB
unless the caller opts in. The Huatu discovery URL is provenance only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from register_phase4_hbdk_candidates import EVIDENCE, _load, build_leads


ROOT = Path(__file__).resolve().parent.parent
SOURCE_ID = "hbdk-official-recruitment"


def _settings_for(database_path: Path) -> Settings:
    import os

    os.environ["APP_DATABASE_PATH"] = str(database_path)
    os.environ["APP_DATA_DIR"] = str(database_path.parent)
    load_project_env(ROOT / ".env")
    return Settings.from_env()


def _source(database: Database) -> dict:
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    source = next((item for item in registry if item.get("id") == SOURCE_ID), None)
    if not isinstance(source, dict):
        raise ValueError(f"{SOURCE_ID} is missing from data/sources.json")
    database.upsert_source(source)
    return source


def promote(database_path: Path, evidence_path: Path) -> dict[str, object]:
    payload = _load(evidence_path)
    deadline = payload["official_deadline"]
    deadline_date = str(deadline["resolved_calendar_date"])
    settings = _settings_for(database_path)
    database = Database(database_path)
    database.initialize()
    source = _source(database)
    pipeline = JobPipeline(settings, database)
    leads = build_leads(payload)
    promoted: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    existing_leads = database.list_candidate_leads(
        discovery_source_id="huatu-discovery", limit=5000
    )
    for lead in leads:
        row = lead["metadata"]["row"]
        row_number = int(row["row_number"])
        stored = next(
            (
                item
                for item in existing_leads
                if isinstance(item.get("metadata"), dict)
                and item["metadata"].get("batch_id") == lead["metadata"]["batch_id"]
                and int((item["metadata"].get("row") or {}).get("row_number") or -1)
                == row_number
            ),
            None,
        )
        if stored is None:
            raise ValueError(f"HBDK candidate row {row_number} is not registered")
        if stored.get("verification_status") == "published":
            database.update_candidate_lead(
                int(stored["id"]),
                {
                    "conversion_status": "official_content_verified",
                },
            )
            skipped.append({"lead_id": int(stored["id"]), "reason": "already_published"})
            continue
        if stored.get("verification_status") != "official_content_verified":
            raise ValueError(
                f"HBDK candidate {stored['id']} is not official_content_verified: "
                f"{stored.get('verification_status')}"
            )

        field_evidence = {
            "evidence_scope": "official_attachment_row",
            "岗位": row["title"],
            "用人单位": lead["employer_hint"],
            "专业要求": row["major_requirement"],
            "学历要求": row["degree_requirement"],
            "工作地点": row["location"],
            "招聘人数": str(row["headcount"]),
            "岗位类别": row.get("position_category") or "",
            "部门": row.get("department") or "",
            "岗位方向": row.get("department") or "",
            "官方发布日期": deadline["published_date"],
            "官方截止日期原文": deadline["text"],
            "官方报名截止日期": deadline_date,
            "官方岗位表链接": payload["source"]["official_table_url"],
            "官方岗位表哈希": payload["capture"]["sha256"],
            "官方岗位表行号": str(row_number),
            "官方报名方式": "将报名表、承诺书及证件材料按官方公告要求发送至 hbdqwl2020@163.com。",
            "官方公告链接": payload["source"]["official_notice_url"],
        }
        raw = RawPosting(
            title=str(row["title"]),
            employer=str(lead["employer_hint"]),
            source_url=str(payload["source"]["official_notice_url"]),
            application_url=None,
            text=(
                f"官方招聘岗位：{row['title']}。"
                f"专业要求：{row['major_requirement']}。"
                f"学历要求：{row['degree_requirement']}。"
                f"报名方式：将材料发送至 hbdqwl2020@163.com。"
            ),
            summary=(
                f"河北省地球物理勘查有限公司公开招聘岗位；"
                f"官方报名截止日期按公告发布之日起7个北京时间工作日计算为{deadline_date}。"
            ),
            published_date=str(deadline["published_date"]),
            deadline_date=deadline_date,
            location=str(row["location"]),
            external_id=f"huatu-hbdk-2026-10-15-row-{row_number}",
            match_text=(
                f"{row['title']} {lead['employer_hint']} "
                f"{row['major_requirement']} {row['location']}"
            ),
            official_evidence_url=str(payload["source"]["official_notice_url"]),
            field_evidence=field_evidence,
            qualification_text=(
                f"专业要求：{row['major_requirement']}；"
                f"学历要求：{row['degree_requirement']}"
            ),
        )
        job = pipeline.normalize_posting(raw, source)
        job.update(
            {
                "trust_grade": "A",
                "third_party_source_id": payload["source"]["discovery_source_id"],
                "third_party_discovery_url": payload["source"]["discovery_page_url"],
                "third_party_batch_id": "huatu-hbdk-2026-10-15",
                "third_party_discovery_sha256": None,
                "official_notice_url": payload["source"]["official_notice_url"],
                "official_table_url": payload["source"]["official_table_url"],
                "official_application_url": None,
                "third_party_table_url": None,
                "table_sha256": payload["capture"]["sha256"],
                "table_row_number": row_number,
                "last_official_verified_at": payload["captured_at"],
                "no_deadline_expires_at": None,
            }
        )
        if job.get("publication_status") not in {"student_eligible", "unrestricted_eligible"}:
            raise ValueError(
                f"HBDK row {row_number} failed publication gate: "
                f"{job.get('publication_status')} / {job.get('publication_basis')}"
            )
        job_id, outcome = database.save_job(job)
        database.mark_candidate_lead_published(int(stored["id"]), job_id)
        database.update_candidate_lead(
            int(stored["id"]),
            {
                "conversion_status": "official_content_verified",
                "verification_note": (
                    "官方公告、官方岗位表及对应岗位行已核验；"
                    "相对期限按北京时间工作日规则解析并已创建正式岗位。"
                ),
            },
        )
        promoted.append(
            {
                "lead_id": int(stored["id"]),
                "job_id": job_id,
                "row_number": row_number,
                "outcome": outcome,
                "publication_status": job.get("publication_status"),
                "publication_layer": database.find_job(job_id, student_visible=False).get(
                    "publication_layer"
                ),
            }
        )
    return {"promoted": promoted, "skipped": skipped, "count": len(promoted)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    path = args.database.resolve()
    formal = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if not args.apply:
        payload = _load(args.evidence)
        print(json.dumps({"mode": "dry_run", "rows": len(build_leads(payload))}, ensure_ascii=False, indent=2))
        return 0
    if path == formal and not args.allow_formal_database:
        raise SystemExit("refusing to write runtime/job_hub.sqlite3 without --allow-formal-database")
    print(json.dumps(promote(path, args.evidence), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
