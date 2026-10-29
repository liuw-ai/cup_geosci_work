"""Produce a compact, read-only explanation of the remaining publication gap."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_hub.db import Database
from job_hub.profiles import evaluate_student_publication


def _current(job: dict[str, Any], as_of: str) -> bool:
    if str(job.get("status") or "") not in {"open", "expired"}:
        return False
    deadline = str(job.get("deadline_date") or "").strip()
    if deadline:
        try:
            return date.fromisoformat(deadline) >= date.fromisoformat(as_of)
        except ValueError:
            return False
    expiry = str(job.get("no_deadline_expires_at") or "").strip()
    return not expiry or expiry[:10] >= as_of


def build_report(
    database_path: Path,
    as_of: str,
    *,
    include_extended_opportunities: bool = False,
) -> dict[str, Any]:
    database = Database(database_path)
    jobs, _ = database.list_jobs(
        page_size=None, student_visible=False, only_open=False, as_of_date=as_of
    )
    visible_jobs, visible_count = database.list_jobs(
        page_size=None, student_visible=True, only_open=True, as_of_date=as_of
    )
    pending: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    source_counts: Counter[tuple[str, str]] = Counter()
    unique_keys: dict[str, set[str]] = {}
    for job in jobs:
        if not _current(job, as_of):
            continue
        decision = evaluate_student_publication(
            job,
            include_extended_opportunities=include_extended_opportunities,
        )
        counts[f"{decision.status}:{decision.label}"] += 1
        source_counts[(decision.status, str(job.get("source_id") or "unknown"))] += 1
        class_key = f"{decision.status}:{decision.label}"
        unique_keys.setdefault(class_key, set()).add(
            str(job.get("official_detail_key") or f"job:{job.get('id')}")
        )
        item = {
            "id": job.get("id"),
            "title": job.get("title"),
            "employer": job.get("employer"),
            "source_id": job.get("source_id"),
            "source_name": job.get("source_name"),
            "location": job.get("location"),
            "deadline_date": job.get("deadline_date"),
            "persisted_publication_status": job.get("publication_status"),
            "persisted_publication_layer": job.get("publication_layer"),
            "source_availability_status": job.get("source_availability_status"),
            "last_official_verified_at": job.get("last_official_verified_at"),
            "country_or_region": job.get("country_or_region"),
            "official_detail_key": job.get("official_detail_key"),
            "status": job.get("status"),
            "no_deadline_expires_at": job.get("no_deadline_expires_at"),
            "recomputed_status": decision.status,
            "decision_label": decision.label,
            "decision_reason": decision.reason,
            "official_url": job.get("official_evidence_url") or job.get("source_url"),
            "major_tags": job.get("major_tags"),
            "field_evidence": job.get("field_evidence"),
            "qualification_text": job.get("qualification_text"),
            "description": str(job.get("description") or "")[:1000],
        }
        if decision.status == "pending_evidence":
            pending.append(item)
        elif decision.is_public:
            eligible.append(item)
    return {
        "report": "phase72_gap_analysis",
        "database": str(database_path),
        "as_of_date": as_of,
        "include_extended_opportunities": include_extended_opportunities,
        "read_only": True,
        "current_recomputed_counts": dict(counts),
        "current_recomputed_unique_detail_counts": {
            key: len(values) for key, values in sorted(unique_keys.items())
        },
        "current_recomputed_union_counts": {
            "public_and_pending": len(
                set().union(
                    *[
                        values
                        for key, values in unique_keys.items()
                        if key.startswith("student_eligible:")
                        or key.startswith("pending_evidence:")
                    ]
                )
            ),
            "public_pending_social_unrestricted": len(
                set().union(
                    *[
                        values
                        for key, values in unique_keys.items()
                        if key.startswith("student_eligible:")
                        or key.startswith("pending_evidence:")
                        or key == "out_of_scope:社会招聘岗位"
                        or key == "out_of_scope:非公共岗位不限专业"
                    ]
                )
            ),
            "public_job_level_pending_social_unrestricted": len(
                set().union(
                    *[
                        values
                        for key, values in unique_keys.items()
                        if key.startswith("student_eligible:")
                        or key == "pending_evidence:待补岗位级证据"
                        or key == "out_of_scope:社会招聘岗位"
                        or key == "out_of_scope:非公共岗位不限专业"
                    ]
                )
            ),
        },
        "canonical_student_visible_count": visible_count,
        "canonical_student_visible_ids": [int(job["id"]) for job in visible_jobs],
        "current_recomputed_source_counts": [
            {"status": status, "source_id": source, "count": count}
            for (status, source), count in source_counts.most_common()
        ],
        "pending_jobs": pending,
        "eligible_jobs": eligible,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extended", action="store_true")
    args = parser.parse_args()
    report = build_report(
        args.database,
        args.as_of,
        include_extended_opportunities=args.extended,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["current_recomputed_counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
