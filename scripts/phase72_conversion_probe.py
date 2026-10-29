"""Read-only conversion probe for verified Phase 7.2 candidates."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_hub.artifact_publication import _normalized_candidate
from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.lead_publication import normalize_verified_candidate_lead
from job_hub.pipeline import JobPipeline


def _settings(database_path: Path) -> Settings:
    load_project_env()
    os.environ["APP_DATABASE_PATH"] = str(database_path.resolve())
    os.environ["APP_DATA_DIR"] = str(database_path.resolve().parent)
    return Settings.from_env()


def _candidate_probe(database: Database, pipeline: JobPipeline) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in database.iter_artifact_job_candidates(
        review_status="official_content_verified"
    ):
        base = {
            "kind": "government_artifact",
            "candidate_id": int(candidate["id"]),
            "source_id": candidate.get("source_id"),
            "title": candidate.get("title"),
            "employer": candidate.get("employer"),
            "review_status": candidate.get("review_status"),
        }
        try:
            normalized = _normalized_candidate(pipeline, database, candidate)
        except (TypeError, ValueError, KeyError) as error:
            base.update({"decision": "blocked", "reason": str(error)})
        else:
            base.update(
                {
                    "decision": "eligible",
                    "publication_status": normalized.get("publication_status"),
                    "publication_layer": normalized.get("publication_layer"),
                    "deadline_date": normalized.get("deadline_date"),
                    "location": normalized.get("location"),
                    "major_tags": normalized.get("major_tags"),
                }
            )
        items.append(base)
    return items


def _lead_probe(database: Database, pipeline: JobPipeline) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for lead in database.list_candidate_leads(
        "official_content_verified", limit=5000
    ):
        base = {
            "kind": "third_party_lead",
            "lead_id": int(lead["id"]),
            "discovery_source_id": lead.get("discovery_source_id"),
            "title": lead.get("title"),
            "employer": lead.get("employer_hint"),
            "verification_status": lead.get("verification_status"),
        }
        try:
            normalized = normalize_verified_candidate_lead(database, pipeline, lead)
        except (TypeError, ValueError, KeyError) as error:
            base.update({"decision": "blocked", "reason": str(error)})
        else:
            base.update(
                {
                    "decision": "eligible",
                    "publication_status": normalized.get("publication_status"),
                    "publication_layer": normalized.get("publication_layer"),
                    "deadline_date": normalized.get("deadline_date"),
                    "location": normalized.get("location"),
                    "major_tags": normalized.get("major_tags"),
                }
            )
        items.append(base)
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = _settings(args.database)
    database = Database(args.database)
    pipeline = JobPipeline(settings, database)
    candidates = _candidate_probe(database, pipeline)
    leads = _lead_probe(database, pipeline)
    report = {
        "report": "phase72_conversion_probe",
        "database": str(args.database),
        "read_only": True,
        "government_artifact_candidates": candidates,
        "third_party_leads": leads,
        "summary": {
            "government_checked": len(candidates),
            "government_eligible": sum(item["decision"] == "eligible" for item in candidates),
            "third_party_checked": len(leads),
            "third_party_eligible": sum(item["decision"] == "eligible" for item in leads),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
