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
from job_hub.profiles import (
    PUBLIC_STUDENT_PUBLICATION_STATUSES,
    evaluate_student_publication,
)


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

# Public-sector and university/research rules may legitimately use a broad
# category, an official catalogue code, or an unrestricted-major rule. Every
# other student-facing category must carry the enterprise four-name match
# marker. Keeping this check in the persisted-data audit catches old rows that
# predate the stricter runtime matcher.
PUBLIC_BROAD_CATEGORIES = frozenset(
    {
        "公务员与选调",
        "事业单位与人才引进",
    }
)
PUBLIC_BROAD_GOVERNMENT_TYPES = frozenset(
    {"public_institution", "civil_service", "postdoctoral"}
)


def _audit_uses_public_broad_rule(job: dict[str, Any]) -> bool:
    if str(job.get("category") or "") in PUBLIC_BROAD_CATEGORIES:
        return True
    evidence = job.get("field_evidence")
    return isinstance(evidence, dict) and str(
        evidence.get("政府岗位类型") or ""
    ).strip() in PUBLIC_BROAD_GOVERNMENT_TYPES


def audit_database(
    database: Database,
    settings: Settings,
    today: date | None = None,
) -> dict[str, Any]:
    """Check persisted jobs against the registered official-source contract."""
    target_date = today or datetime.now(ZoneInfo(settings.timezone)).date()
    sources = {item["id"]: item for item in database.list_sources()}
    _, total = database.list_jobs(
        page=1,
        page_size=1,
        only_open=False,
        student_visible=False,
    )
    jobs, _ = database.list_jobs(
        page=1,
        page_size=max(total, 1),
        only_open=False,
        student_visible=False,
    )
    # Keep the historical ``open_jobs`` metric for audit compatibility, but
    # also expose the exact dated student-facing count.  The former only
    # reflects materialized status/publication fields; the latter applies the
    # same deadline, source freshness and evidence CTE as the public API.
    current_public_jobs, _ = database.list_jobs(
        page_size=None,
        as_of_date=target_date.isoformat(),
    )
    issues: list[dict[str, Any]] = []
    source_urls: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    verified_evidence_job_ids = database.verified_official_evidence_job_ids()

    for job in jobs:
        job_id = int(job["id"])
        source_id = str(job.get("source_id") or "")
        student_public = (
            job.get("status") == "open"
            and (
                job.get("publication_status") in PUBLIC_STUDENT_PUBLICATION_STATUSES
                or job.get("publication_layer") in {
                    "exact_match",
                    "broad_geoscience_match",
                    "incomplete_but_basic_match",
                    "source_unavailable",
                }
            )
        )
        # Official but out-of-scope records stay available for administrator
        # re-review. They are not part of a student-facing daily publication
        # and therefore must not make that publication fail.
        if not student_public:
            continue
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
            job.get("relevance_band") == "强相关"
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
        if (
            job.get("publication_layer") not in {
                "incomplete_but_basic_match", "source_unavailable"
            }
            and
            job.get("publication_status") in PUBLIC_STUDENT_PUBLICATION_STATUSES
            and not job.get("publication_basis", {}).get("matched_profile_ids")
        ):
            issues.append(
                {
                    "code": "public_job_without_supported_student_profile",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": "学生端公开岗位缺少岗位级专业或不限专业的适配依据。",
                }
            )
        if (
            job.get("publication_layer") not in {
                "broad_geoscience_match", "incomplete_but_basic_match", "source_unavailable"
            }
            and
            job.get("publication_status")
            in PUBLIC_STUDENT_PUBLICATION_STATUSES
            and not _audit_uses_public_broad_rule(job)
            and job.get("publication_basis", {}).get("major_match_mode")
            not in {"exact_four_names", "similar_major"}
        ):
            issues.append(
                {
                    "code": "enterprise_job_without_exact_four_major_mode",
                    "job_id": job_id,
                    "source_id": source_id,
                    "message": (
                        "企业岗位缺少 exact_four_names 或 similar_major 专业门禁标记；"
                        "必须先重索引并重新核验岗位级专业证据。"
                    ),
                }
            )
        if (
            job.get("publication_layer")
            not in {"broad_geoscience_match", "incomplete_but_basic_match", "source_unavailable"}
            and not _audit_uses_public_broad_rule(job)
        ):
            decision = evaluate_student_publication(job)
            declared_mode = str(
                job.get("publication_basis", {}).get("major_match_mode") or ""
            )
            if (
                decision.status != job.get("publication_status")
                or declared_mode != decision.major_match_mode
            ):
                issues.append(
                    {
                        "code": "public_job_publication_decision_mismatch",
                        "job_id": job_id,
                        "source_id": source_id,
                        "message": (
                            "学生端公开状态与当前岗位级专业/学历门禁不一致；"
                            f"声明={job.get('publication_status')}/{declared_mode}，"
                            f"复核={decision.status}/{decision.major_match_mode}。"
                        ),
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
        elif source.get("source_type") != "manual":
            # ``source_url`` is checked below, but the link students actually
            # need may be an attachment or a detail host declared separately
            # by the adapter.  Validate that evidence URL against the same
            # source boundary so a bad import cannot silently point elsewhere.
            evidence_allowed_hosts = {
                str(host).lower().rstrip(".")
                for host in (
                    list(source.get("config", {}).get("allowed_hosts", []))
                    + list(source.get("config", {}).get("detail_allowed_hosts", []))
                    + list(source.get("config", {}).get("attachment_allowed_hosts", []))
                )
            }
            homepage_host = urlparse(str(source.get("homepage_url", ""))).hostname
            if homepage_host:
                evidence_allowed_hosts.add(homepage_host.lower().rstrip("."))
            evidence_host = evidence.hostname.lower().rstrip(".")
            if evidence_allowed_hosts and evidence_host not in evidence_allowed_hosts:
                issues.append(
                    {
                        "code": "official_evidence_host_mismatch",
                        "job_id": job_id,
                        "source_id": source_id,
                        "message": f"官方证据链接域名 {evidence.hostname} 不在来源白名单中。",
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
                for host in (
                    list(source.get("config", {}).get("allowed_hosts", []))
                    + list(source.get("config", {}).get("detail_allowed_hosts", []))
                    + list(source.get("config", {}).get("attachment_allowed_hosts", []))
                )
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
        if int(job.get("relevance_score", 0)) < minimum:
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
                if deadline_value < target_date:
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
                    "job_ids": [job_id for job_id, _, _ in job_entries],
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
        "open_jobs": sum(
            1
            for job in jobs
            if job.get("status") == "open"
            and job.get("publication_status")
            in PUBLIC_STUDENT_PUBLICATION_STATUSES
        ),
        "current_public_jobs": len(current_public_jobs),
        "registered_sources": len(sources),
        "enabled_sources": sum(1 for source in sources.values() if source.get("enabled")),
        "issues": issues,
        "enabled_source_failures": enabled_failures,
        "stale_crawl_runs": stale_crawl_runs,
        "audited_date": target_date.isoformat(),
    }
