from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.matching import has_major_qualification_evidence


FORBIDDEN_JOB_TEXT = (
    "招聘会",
    "宣讲会",
    "双选会",
    "邀请函",
    "就业补贴",
    "采购",
    "招标",
    "中标",
)

NON_VACANCY_TITLE_PATTERNS = (
    r"拟(?:聘|录用|聘用)",
    r"(?:进入|面试)(?:范围|名单)",
    r"递补",
    r"资格(?:审查|复审)",
    r"笔试(?:成绩|公告|结果)",
    r"面试(?:成绩|公告|结果)",
    r"体检(?:公告|名单|结果)",
    r"考察(?:公告|名单|结果)",
    r"录用(?:公示|名单|结果)",
)


def audit_database(
    database: Database,
    settings: Settings,
    today: date | None = None,
) -> dict[str, Any]:
    """Check persisted jobs against the registered official-source contract."""
    target_date = today or datetime.now(ZoneInfo(settings.timezone)).date()
    sources = {item["id"]: item for item in database.list_sources()}
    _, total = database.list_jobs(page=1, page_size=1, only_open=False)
    jobs, _ = database.list_jobs(
        page=1,
        page_size=max(total, 1),
        only_open=False,
    )
    issues: list[dict[str, Any]] = []
    source_urls: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    verified_evidence_job_ids = database.verified_official_evidence_job_ids()

    for job in jobs:
        job_id = int(job["id"])
        source_id = str(job.get("source_id") or "")
        source = sources.get(source_id)
        if source is None:
            issues.append(
                {
                    "code": "unknown_source",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "岗位引用了未注册来源。",
                }
            )
            continue

        source_url = str(job.get("source_url") or "")
        if job.get("status") == "open":
            source_urls[source_url].append(
                (job_id, source_id, str(job.get("external_id") or ""))
            )
        if job.get("verification_status") != "published_official":
            issues.append(
                {
                    "code": "unpublished_job_record",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "候选线索或未完成核验记录不能进入公开岗位库。",
                }
            )
        if (
            job.get("status") == "open"
            and job.get("relevance_band") == "强相关"
            and not has_major_qualification_evidence(job.get("field_evidence"))
        ):
            issues.append(
                {
                    "code": "strong_match_without_major_evidence",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "强相关岗位缺少岗位级专业要求证据。",
                }
            )
        official_evidence_url = str(job.get("official_evidence_url") or "")
        evidence = urlparse(official_evidence_url)
        if evidence.scheme not in {"http", "https"} or not evidence.hostname:
            issues.append(
                {
                    "code": "missing_official_evidence_url",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "公开岗位缺少可访问的官方原文证据链接。",
                }
            )
        if job_id not in verified_evidence_job_ids:
            issues.append(
                {
                    "code": "missing_verified_official_evidence",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "公开岗位缺少已核验的官方页面或官方记录证据。",
                }
            )
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            issues.append(
                {
                    "code": "invalid_source_url",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "原始来源链接不是有效的 HTTP(S) 地址。",
                }
            )
        elif source.get("source_type") != "manual":
            allowed_hosts = {
                str(host).lower()
                for host in source.get("config", {}).get("allowed_hosts", [])
            }
            homepage_host = urlparse(str(source.get("homepage_url", ""))).hostname
            if homepage_host:
                allowed_hosts.add(homepage_host.lower())
            if parsed.hostname.lower() not in allowed_hosts:
                issues.append(
                    {
                        "code": "source_host_mismatch",
                        "job_id": job_id,
                        "source_id": source_id,
                        "message": f"原始链接域名 {parsed.hostname} 不在来源白名单中。",
                    }
                )

        text = f"{job.get('title', '')} {job.get('summary', '')}"
        for keyword in FORBIDDEN_JOB_TEXT:
            if keyword in text:
                issues.append(
                    {
                        "code": "forbidden_notice_text",
                        "job_id": job_id,
                        "source_id": source_id,
                        "message": f"岗位标题或摘要包含非岗位公告关键词：{keyword}。",
                    }
                )
                break

        if any(
            re.search(pattern, str(job.get("title") or ""), re.IGNORECASE)
            for pattern in NON_VACANCY_TITLE_PATTERNS
        ):
            issues.append(
                {
                    "code": "non_vacancy_process_notice",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "岗位标题是招聘流程或结果公告，不是可投递岗位。",
                }
            )

        try:
            minimum = int(source.get("config", {}).get("minimum_relevance", 0))
        except (TypeError, ValueError):
            minimum = 0
            issues.append(
                {
                    "code": "invalid_source_threshold",
                    "source_id": source_id,
                    "message": "来源 minimum_relevance 不是整数。",
                }
            )
        if job.get("status") == "open" and int(job.get("relevance_score", 0)) < minimum:
            issues.append(
                {
                    "code": "below_source_threshold",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "岗位相关度低于当前来源发布阈值。",
                }
            )

        if (
            source.get("source_type") != "manual"
            and not source.get("enabled", False)
            and job.get("status") == "open"
        ):
            issues.append(
                {
                    "code": "disabled_source_job",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "岗位来自已停用来源，不应继续作为在招岗位展示。",
                }
            )

        if not str(job.get("title") or "").strip():
            issues.append(
                {
                    "code": "empty_title",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "岗位标题为空。",
                }
            )
        if not str(job.get("description") or "").strip():
            issues.append(
                {
                    "code": "empty_description",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "岗位正文为空。",
                }
            )

        deadline = job.get("deadline_date")
        if deadline:
            try:
                deadline_value = date.fromisoformat(str(deadline))
            except ValueError:
                issues.append(
                    {
                        "code": "invalid_deadline",
                        "job_id": job_id,
                        "source_id": source_id,
                        "message": "截止日期不是 ISO 日期。",
                    }
                )
            else:
                if job.get("status") == "open" and deadline_value < target_date:
                    issues.append(
                        {
                            "code": "stale_open_job",
                            "job_id": job_id,
                            "source_id": source_id,
                            "message": "截止日期早于审计日期但状态仍为 open。",
                        }
                    )

    for source_url, job_entries in source_urls.items():
        source_ids = {source_id for _, source_id, _ in job_entries}
        shared_url_allowed = all(
            bool(sources.get(source_id, {}).get("config", {}).get("allow_shared_source_url"))
            for source_id in source_ids
        )
        external_ids = {external_id for _, _, external_id in job_entries}
        row_level_records = len(external_ids) == len(job_entries) and all(external_ids)
        if (
            source_url
            and len(job_entries) > 1
            and not shared_url_allowed
            and not row_level_records
        ):
            issues.append(
                {
                    "code": "duplicate_source_url",
                    "job_ids": [job_id for job_id, _ in job_entries],
                    "message": "多个岗位记录共用同一原始来源链接。",
                }
            )

    enabled_failures = []
    for failure in database.recent_crawl_failures(100):
        source = sources.get(str(failure.get("source_id") or ""))
        if source and source.get("enabled"):
            enabled_failures.append(failure)

    stale_crawl_runs = database.stale_crawl_runs(
        settings.crawl_run_stale_seconds
    )
    for crawl_run in stale_crawl_runs:
        source = sources.get(str(crawl_run.get("source_id") or ""))
        if source and source.get("enabled"):
            issues.append(
                {
                    "code": "stale_crawl_run",
                    "source_id": crawl_run.get("source_id"),
                    "crawl_run_id": crawl_run.get("id"),
                    "message": "启用来源存在超时未结束的采集记录，不能发布日报。",
                }
            )

    return {
        "ok": not issues,
        "checked_jobs": len(jobs),
        "total_jobs": total,
        "open_jobs": sum(1 for job in jobs if job.get("status") == "open"),
        "registered_sources": len(sources),
        "enabled_sources": sum(1 for source in sources.values() if source.get("enabled")),
        "issues": issues,
        "enabled_source_failures": enabled_failures,
        "stale_crawl_runs": stale_crawl_runs,
        "audited_date": target_date.isoformat(),
    }
