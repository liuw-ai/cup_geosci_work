"""Promote one verified Huatu-to-GDZYS row in an isolated local database.

The Huatu page is discovery provenance only.  The published row is rebuilt
from the official GDZYS notice, retains the official email application method,
and refuses to invent an HTTP application URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs/current/phase4-third-party-gdzys-2026-10-21.json"
DISCOVERY_SOURCE_ID = "huatu-discovery"
OFFICIAL_SOURCE_ID = "gdzys-official-recruitment"


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    capture = payload["capture"]
    for key in ("discovery_page", "official_notice"):
        file_path = ROOT / str(capture[key])
        if not file_path.is_file():
            raise ValueError(f"missing retained evidence: {file_path}")
        actual = hashlib.sha256(file_path.read_bytes()).hexdigest().upper()
        expected = str(capture[f"{key}_sha256"]).upper()
        if actual != expected:
            raise ValueError(f"evidence hash mismatch for {key}: {actual} != {expected}")
    rows = [row for row in payload["rows"] if row.get("accepted")]
    if len(rows) != 1:
        raise ValueError("expected exactly one accepted GDZYS row")
    return payload


def _settings(database_path: Path) -> Settings:
    os.environ["APP_DATABASE_PATH"] = str(database_path)
    os.environ["APP_DATA_DIR"] = str(database_path.parent)
    load_project_env(ROOT / ".env")
    return Settings.from_env()


def promote(database_path: Path, evidence_path: Path) -> dict[str, object]:
    payload = _load(evidence_path)
    database = Database(database_path)
    database.initialize()
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    sources = {str(item["id"]): item for item in registry}
    # The discovery source is registered in discovery_sources.json, while the
    # official source is registered in sources.json.  They intentionally live
    # in separate registries and must not be conflated.
    if OFFICIAL_SOURCE_ID not in sources:
        raise ValueError(f"{OFFICIAL_SOURCE_ID} is missing from data/sources.json")
    database.upsert_source(sources[OFFICIAL_SOURCE_ID])
    pipeline = JobPipeline(_settings(database_path), database)
    official = payload["official_recovery"]
    discovery = payload["discovery"]
    row = next(item for item in payload["rows"] if item.get("accepted"))
    metadata = {
        "batch_id": "huatu-gdzys-2026-10-16",
        "evidence_report": str(evidence_path.relative_to(ROOT)).replace("\\", "/"),
        "discovery_page_url": discovery["url"],
        "official_notice_url": official["notice_url"],
        "official_notice_sha256": payload["capture"]["official_notice_sha256"],
        "official_application_method": official["application_method"],
        "published_date": official["published_date"],
        "location": official["location"],
        "no_deadline_expires_at": official["no_deadline_expires_at"],
        "row": row,
        "field_evidence": {
            "evidence_scope": "official_notice_row",
            "岗位": row["title"],
            "研究方向": row["research_direction"],
            "用人单位": official["employer"],
            "专业要求": row["major_requirement"],
            "学历要求": row["degree_requirement"],
            "工作地点": official["location"],
            "官方发布日期": official["published_date"],
            "官方公告链接": official["notice_url"],
            "官方公告哈希": payload["capture"]["official_notice_sha256"],
            "官方报名方式": official["application_method"],
            "官方未公布截止日期": official["no_deadline_expires_at"],
        },
    }
    lead = {
        "lead_provider": "华图教育",
        "lead_url": f"{discovery['url']}#official-row-{row['row_number']}",
        "title": row["title"],
        "employer_hint": official["employer"],
        "location_hint": official["location"],
        "province_hint": "广东",
        "published_date": official["published_date"],
        "official_url": official["notice_url"],
        "official_source_id": OFFICIAL_SOURCE_ID,
        "official_application_url": None,
        "official_table_url": None,
        "third_party_table_url": None,
        "trust_grade": "B",
        "discovery_source_id": DISCOVERY_SOURCE_ID,
        "verification_status": "official_content_verified",
        "conversion_status": "official_content_verified",
        "verification_note": "华图发现页已回溯至广东省科学院资源利用与稀土开发研究所官网同标题公告；已逐项核对专业、学历、地点、发布日期和邮箱报名方式。",
        "metadata": metadata,
    }
    stored = database.create_candidate_lead(lead)
    # ``create_candidate_lead`` deliberately starts every new lead at
    # ``candidate`` and performs only a domain assessment.  Promote it through
    # the normal evidence transition so the publication link is auditable and
    # the database enforces the registered official source contract.
    if stored.get("verification_status") != "published":
        if stored.get("verification_status") == "candidate":
            stored = database.update_candidate_lead(
                int(stored["id"]),
                {
                    "official_url": official["notice_url"],
                    "official_source_id": OFFICIAL_SOURCE_ID,
                    "official_domain_status": "registered_source_match",
                    "trust_grade": "B",
                    "official_table_url": None,
                    "third_party_table_url": None,
                    "verification_status": "official_url_found",
                    "verification_note": "已从华图发现页回溯到同标题官方招聘公告。",
                    "metadata": metadata,
                },
            )
        stored = database.update_candidate_lead(
            int(stored["id"]),
            {
                "official_url": official["notice_url"],
                "official_source_id": OFFICIAL_SOURCE_ID,
                "official_domain_status": "registered_source_match",
                "trust_grade": "B",
                "official_table_url": None,
                "third_party_table_url": None,
                "verification_status": "official_content_verified",
                "conversion_status": "official_content_verified",
                "verification_note": lead["verification_note"],
                "metadata": metadata,
            },
        )
    raw = RawPosting(
        title=row["title"],
        employer=official["employer"],
        source_url=official["notice_url"],
        application_url=None,
        text=(
            f"官方招聘公告岗位：{row['title']}。"
            f"研究方向：{row['research_direction']}。"
            f"专业要求：{row['major_requirement']}。"
            f"学历要求：{row['degree_requirement']}。"
            f"报名方式：{official['application_method']}"
        ),
        summary="广东省科学院资源利用与稀土开发研究所矿产资源研究方向人才招聘；官方未公布固定截止日期。",
        published_date=official["published_date"],
        deadline_date=None,
        location=official["location"],
        external_id="huatu-gdzys-2026-10-16-row-1",
        match_text=f"{row['title']} {official['employer']} {row['major_requirement']} {official['location']}",
        official_evidence_url=official["notice_url"],
        field_evidence=metadata["field_evidence"],
        qualification_text=f"专业要求：{row['major_requirement']}；学历要求：{row['degree_requirement']}",
    )
    job = pipeline.normalize_posting(raw, sources[OFFICIAL_SOURCE_ID])
    job.update(
        {
            "trust_grade": "A",
            "third_party_source_id": DISCOVERY_SOURCE_ID,
            "third_party_discovery_url": discovery["url"],
            "third_party_batch_id": metadata["batch_id"],
            "third_party_discovery_sha256": payload["capture"]["discovery_page_sha256"],
            "official_notice_url": official["notice_url"],
            "official_table_url": None,
            "official_application_url": None,
            "last_official_verified_at": payload["capture"]["captured_at"],
            "no_deadline_expires_at": official["no_deadline_expires_at"],
        }
    )
    if job.get("publication_status") not in {"student_eligible", "unrestricted_eligible"}:
        raise ValueError(
            "GDZYS row failed publication gate: "
            f"status={job.get('publication_status')} "
            f"layer={job.get('publication_layer')} "
            f"basis={job.get('publication_basis')}"
        )
    job_id, outcome = database.save_job(job)
    database.mark_candidate_lead_published(int(stored["id"]), job_id)
    return {
        "candidate_lead_id": int(stored["id"]),
        "job_id": job_id,
        "outcome": outcome,
        "publication_status": job.get("publication_status"),
        "publication_layer": database.find_job(job_id, student_visible=False).get("publication_layer"),
        "official_notice_url": official["notice_url"],
        "official_application_url": None,
        "no_deadline_expires_at": official["no_deadline_expires_at"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        payload = _load(args.evidence)
        print(json.dumps({"mode": "dry_run", "accepted_rows": 1, "rejected_rows": 1}, ensure_ascii=False, indent=2))
        return 0
    path = args.database.resolve()
    formal = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if path == formal and not args.allow_formal_database:
        raise SystemExit("refusing to write runtime/job_hub.sqlite3 without --allow-formal-database")
    print(json.dumps(promote(path, args.evidence), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
