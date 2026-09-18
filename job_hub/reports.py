from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database


def local_today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def job_card(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "title": job["title"],
        "employer": job["employer"],
        "category": job["category"],
        "source_tier": job["source_tier"],
        "location": job.get("location"),
        "deadline_date": job.get("deadline_date"),
        "degree_levels": job.get("degree_levels", []),
        "major_tags": job.get("major_tags", []),
        "relevance_score": job["relevance_score"],
        "relevance_band": job["relevance_band"],
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
    }


def publish_daily_report(
    database: Database,
    settings: Settings,
    report_date: date | None = None,
) -> dict[str, Any]:
    target_date = report_date or local_today(settings)
    existing = database.get_daily_report(target_date.isoformat())
    if existing is not None:
        return existing
    report = build_daily_report(database, settings, target_date)
    database.save_daily_report(report["report_date"], report)
    return report
