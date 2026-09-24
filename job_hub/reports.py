from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.government_artifacts import (
    government_artifact_refresh_summary,
    load_government_artifact_manifest,
)
from job_hub.government_positions import (
    government_position_quality_report,
    load_position_registry,
)


def local_today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def job_card(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "title": job["title"],
        "employer": job["employer"],
        "canonical_employer_id": job.get("canonical_employer_id"),
        "canonical_employer_name": job.get("canonical_employer_name"),
        "parent_employer_name": job.get("parent_employer_name"),
        "category": job["category"],
        "source_tier": job["source_tier"],
        "employment_path": job.get("employment_path", job["category"]),
        "employer_type": job.get("employer_type", "单位属性待核验"),
        "affiliation": job.get("affiliation", "所属体系待核验"),
        "opportunity_scope": job.get("opportunity_scope", "地域待核验"),
        "role_direction": job.get("role_direction", "方向待核验"),
        "location": job.get("location"),
        "province": job.get("province"),
        "city": job.get("city"),
        "country_or_region": job.get("country_or_region"),
        "deadline_date": job.get("deadline_date"),
        "degree_levels": job.get("degree_levels", []),
        "major_tags": job.get("major_tags", []),
        "field_evidence": job.get("field_evidence", {}),
        "relevance_score": job["relevance_score"],
        "relevance_band": job["relevance_band"],
        "publication_status": job.get("publication_status"),
        "publication_basis": job.get("publication_basis", {}),
        "source_name": job["source_name"],
        "source_url": job["source_url"],
        "application_url": job.get("application_url"),
        "summary": job.get("summary", ""),
    }


def build_daily_report(
    database: Database,
    settings: Settings,
    report_date: date | None = None,
) -> dict[str, Any]:
    target_date = report_date or local_today(settings)
    target = target_date.isoformat()
    database.expire_jobs_before(target)
    changes = database.jobs_for_report(target, settings.timezone)
    new_ids = {job["id"] for job in changes["new"]}
    updated = [job for job in changes["updated"] if job["id"] not in new_ids]
    deadline_jobs = database.upcoming_deadlines(
        target,
        (target_date + timedelta(days=7)).isoformat(),
    )
    manifest = None
    manifest_error = None
    manifest_path = settings.government_artifact_manifest_path
    if manifest_path is not None:
        try:
            manifest = load_government_artifact_manifest(manifest_path)
        except (OSError, ValueError) as error:
            # A malformed manifest must be visible in operations, but must not
            # prevent an already verified daily report from being generated.
            manifest = {"as_of": None, "artifacts": []}
            manifest_error = str(error)
    government_quality = government_artifact_refresh_summary(
        database,
        manifest=manifest,
        today=target,
        manifest_error=manifest_error,
    )
    position_registry_error = None
    position_quality = None
    registry_path = settings.government_position_registry_path
    if registry_path is not None:
        try:
            position_quality = government_position_quality_report(
                load_position_registry(registry_path), today=target
            )
        except (OSError, ValueError) as error:
            position_registry_error = str(error)
            position_quality = {
                "verified_open_records": 0,
                "explicit_student_matches": 0,
                "source_failures_or_pending": 1,
                "scan_interpretation": "职位表台账不可用，不能解释为无岗位。",
            }
    return {
        "report_date": target,
        "new_jobs": [job_card(job) for job in changes["new"]],
        "updated_jobs": [job_card(job) for job in updated],
        "deadline_jobs": [job_card(job) for job in deadline_jobs],
        "stats": {
            "new": len(changes["new"]),
            "updated": len(updated),
            "deadline_soon": len(deadline_jobs),
            "open_total": database.count_open_jobs(),
        },
        "government_quality": government_quality,
        "government_positions": {
            **(position_quality or {}),
            "registry_error": position_registry_error,
        },
    }


def publish_daily_report(
    database: Database,
    settings: Settings,
    report_date: date | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    target_date = report_date or local_today(settings)
    existing = database.get_daily_report(target_date.isoformat())
    if existing is not None and not force_refresh:
        return existing
    report = build_daily_report(database, settings, target_date)
    database.save_daily_report(report["report_date"], report)
    return report
