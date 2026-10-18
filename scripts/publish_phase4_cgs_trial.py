"""Promote the verified Huatu-to-CGS attachment rows in an explicit local run.

The discovery page remains private provenance.  Each public row is rebuilt
from the official CGS attachment, and the official attachment URL is retained
as the evidence/table link.  No third-party URL is used as an application URL.
The formal runtime database is refused unless the caller explicitly opts in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from register_phase4_cgs_trial import EVIDENCE_PATH, _load_evidence, build_leads


ROOT = Path(__file__).resolve().parent.parent


def _settings_for(database_path: Path) -> Settings:
    os.environ["APP_DATABASE_PATH"] = str(database_path)
    os.environ["APP_DATA_DIR"] = str(database_path.parent)
    load_project_env(ROOT / ".env")
    return Settings.from_env()


def promote(database_path: Path, evidence_path: Path) -> dict[str, object]:
    settings = _settings_for(database_path)
    database = Database(database_path)
    database.initialize()
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    source = next(item for item in registry if item.get("id") == "cgs-notices")
    database.upsert_source(source)
    pipeline = JobPipeline(settings, database)
    payload = _load_evidence(evidence_path)
    leads = build_leads(payload)
    promoted: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for lead in leads:
        stored = database.create_candidate_lead(lead)
        lead_id = int(stored["id"])
        metadata = dict(lead["metadata"])
        # Keep the candidate row complete even when this script is run against
        # a database created by the earlier private-review script.
        database.update_candidate_lead(
            lead_id,
            {
                "official_url": lead["official_url"],
                "official_source_id": lead["official_source_id"],
                "official_table_url": lead["official_table_url"],
                "third_party_table_url": None,
                "official_application_url": None,
                "official_domain_status": "registered_source_match",
                "verification_status": (
                    "official_content_verified"
                    if stored["verification_status"] != "published"
                    else "published"
                ),
                "conversion_status": (
                    "official_content_verified"
                    if stored["verification_status"] != "published"
                    else stored.get("conversion_status") or "official_content_verified"
                ),
                "verification_note": (
                    "已逐行核验中国地质调查局官方博士后计划附件；"
                    "岗位级专业、学历、地点、发布日期、官方岗位表哈希和报名方式均已绑定。"
                ),
                "metadata": metadata,
                "trust_grade": "B",
            },
        )
        refreshed = database.get_candidate_lead(lead_id)
        if refreshed is None:
            raise RuntimeError(f"candidate lead {lead_id} disappeared")
        if refreshed["verification_status"] == "published":
            skipped.append({"lead_id": lead_id, "reason": "already_published"})
            continue

        row = metadata["row"]
        field_evidence = dict(metadata["field_evidence"])
        field_evidence["第三方发现页"] = payload["discovery"]["discovery_page_url"]
        raw = RawPosting(
            title=str(row["title"]),
            employer=str(payload["official_recovery"]["employer"]),
            source_url=str(payload["official_recovery"]["official_table_url"]),
            application_url=None,
            text=(
                f"官方博士后计划岗位：{row['title']}。"
                f"专业要求：{row['major_requirement']}。"
                f"学历要求：{row['degree_requirement']}。"
                f"报名方式：{payload['official_recovery']['official_application_method']}"
            ),
            summary=(
                f"中国地质调查局自然资源综合调查指挥中心博士后岗位；"
                f"官方未公布具体截止日期。"
            ),
            published_date=str(payload["official_recovery"]["published_date"]),
            deadline_date=None,
            location=str(payload["official_recovery"]["location"]),
            external_id=f"huatu-cgs-postdoc-2026-09-30-row-{row['row_number']}",
            match_text=str(metadata["match_text"]),
            official_evidence_url=str(payload["official_recovery"]["official_table_url"]),
            field_evidence=field_evidence,
            qualification_text=str(metadata["qualification_text"]),
        )
        job = pipeline.normalize_posting(raw, source)
        job.update(
            {
                "trust_grade": "A",
                "official_notice_url": str(payload["official_recovery"]["official_table_url"]),
                "official_table_url": str(payload["official_recovery"]["official_table_url"]),
                "official_application_url": None,
                "third_party_table_url": None,
                "table_sha256": str(payload["official_recovery"]["official_table_sha256"]),
                "table_row_number": int(row["row_number"]),
                "last_official_verified_at": str(payload["official_recovery"]["official_table_captured_at"]),
                "no_deadline_expires_at": str(payload["official_recovery"]["no_deadline_expires_at"]),
            }
        )
        if job.get("publication_status") not in {"student_eligible", "unrestricted_eligible"}:
            raise ValueError(
                f"row {row['row_number']} failed publication gate: "
                f"{job.get('publication_status')} / {job.get('publication_basis')}"
            )
        job_id, outcome = database.save_job(job)
        database.mark_candidate_lead_published(lead_id, job_id)
        promoted.append(
            {
                "lead_id": lead_id,
                "job_id": job_id,
                "row_number": row["row_number"],
                "outcome": outcome,
                "publication_status": job.get("publication_status"),
                "publication_layer": database.find_job(job_id, student_visible=False).get("publication_layer"),
            }
        )
    return {"promoted": promoted, "skipped": skipped, "count": len(promoted)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    path = args.database.resolve()
    formal = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if not args.apply:
        print(json.dumps({"mode": "dry_run", "rows": len(build_leads(_load_evidence(args.evidence)))}, ensure_ascii=False, indent=2))
        return
    if path == formal and not args.allow_formal_database:
        raise SystemExit("refusing to write runtime/job_hub.sqlite3 without --allow-formal-database")
    print(json.dumps(promote(path, args.evidence), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
