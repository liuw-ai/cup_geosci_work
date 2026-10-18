"""Private discovery-source registry and lead-funnel helpers.

Discovery channels are useful for finding notices that an official crawler has
not reached yet.  They are deliberately kept outside ``data/sources.json`` and
cannot make a job public by themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from job_hub.contracts import ContractValidationError, validate_discovery_source_registry
from job_hub.profiles import (
    JOB_LEVEL_EVIDENCE_SCOPES,
    PUBLICATION_STUDENT_ELIGIBLE,
    classify_major_match,
    evaluate_student_publication,
)
from job_hub.readiness_axes import is_domestic_job, is_public_job


NON_RECRUITMENT_OFFICIAL_HOSTS = frozenset(
    {"beian.miit.gov.cn", "beian.mps.gov.cn", "beian.gov.cn"}
)


def is_non_recruitment_official_host(host: str) -> bool:
    """Reject filing/registration hosts, including their subdomains."""

    normalized = str(host or "").lower().rstrip(".")
    return bool(
        normalized
        and any(
            normalized == blocked or normalized.endswith("." + blocked)
            for blocked in NON_RECRUITMENT_OFFICIAL_HOSTS
        )
    )


def normalize_recovered_url(value: object) -> str:
    """Remove wrapper punctuation accidentally captured after a URL."""

    url = str(value or "").strip().split("#", 1)[0]
    url = re.sub(r"[，。；！？）】》]+$", "", url)
    url = url.rstrip(".,;:!?]}\"")
    # Text such as ``https://example.gov.cn/)打印准考证`` is common in copied
    # articles. A closing parenthesis followed only by prose is wrapper text,
    # not part of the destination URL.
    if ")" in url:
        prefix, suffix = url.split(")", 1)
        if suffix and not re.search(r"[/?#=&]", suffix):
            url = prefix
    return url.rstrip(".,;:!?)]}")


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "discovery_sources.json"

# Discovery pages contain a large amount of site navigation.  Those links are
# useful for a human browsing the aggregator, but they are not vacancy clues:
# an application login, exam landing page, or category homepage cannot be
# traced to a job-level official announcement.  Keep this filter deliberately
# narrow so real article pages such as ``/2026/0901/2872149.html`` remain
# private clues for official recovery.
_NAVIGATION_TITLE_RE = re.compile(
    r"^(?:各省公务员|国家公务员|省考公务员|事业单位考试|医疗事业单位|"
    r"银行招聘|农信社招聘|央行招聘|事业单位|医疗招聘|教师招聘|医疗卫生招聘)$",
    re.IGNORECASE,
)
_NAVIGATION_TITLE_MARKERS = (
    "报名入口",
    "报名确认",
    "考试费用",
    "报名流程",
    "报名条件",
    "准考证",
    "打印入口",
    "成绩查询",
    "题库",
)
# Aggregator sections often mix real vacancy notices with exam-preparation
# articles.  These markers are intentionally vacancy-specific: a title that
# contains ``考试`` alone is still allowed because official civil-service
# notices commonly use ``考试录用``.  The combination below identifies pages
# that cannot yield an official vacancy row or position table.
_NON_VACANCY_TITLE_MARKERS = (
    "报名人数",
    "竞争比",
    "考试科目",
    "考试时间",
    "考试大纲",
    "申论",
    "行测",
    "真题",
    "模拟题",
    "答案解析",
    "备考",
    "复习",
    "辅导",
    "培训",
    "课程",
    "成绩查询",
    "成绩排名",
    "分数线",
    "面试名单",
    "面试人员名单",
    "考试材料",
    "解题技巧",
    "资格审查名单",
    "缴费入口",
    "准考证打印",
    "政策解读",
    "热点",
)
_NON_VACANCY_PATH_RE = re.compile(
    r"/(?:shenlun|xingce|zhenti|moniti|beikao|peixun|kecheng|"
    r"jiaofu|kaoshi|chengji|mianshi)(?:/|$)",
    re.IGNORECASE,
)
_NAVIGATION_PATH_RE = re.compile(
    r"/(?:[a-z]{2,8}gwy|gwy|guojia|sydw|yldw|yhzp|nxszp|rm|zkxx|yhzp_gg|sydwggk)(?:/|$)"
    r"|/z/(?:gk[a-z0-9_-]+)(?:/|$)"
    r"|/course\.shtml$",
    re.IGNORECASE,
)
_OFFICIAL_NON_VACANCY_PATH_RE = re.compile(
    r"/(?:auth/login|business/home/gkhome|entrylists|login|signin|register)"
    r"(?:\.html?)?/?$",
    re.IGNORECASE,
)
_NON_VACANCY_LINK_LABEL_RE = re.compile(
    r"报名(?:登记|申请|信息|确认|表)|报名表|信息更改申请|诚信承诺|同意报考证明|"
    r"专业目录|考试大纲|笔试|面试名单|综合成绩|成绩公示|录用公示|"
    r"application\s*form|registration\s*form",
    re.IGNORECASE,
)

_RESULT_OR_PUBLICITY_MARKERS = (
    "成绩",
    "分数线",
    "笔试合格",
    "面试",
    "进面",
    "资格审查",
    "资格复审",
    "体检",
    "考察",
    "拟录用",
    "录用公示",
    "公示",
    "排名",
)
_TRAINING_OR_AD_MARKERS = (
    "培训",
    "课程",
    "教材",
    "冲刺",
    "答疑",
    "技巧",
    "解题",
    "考点",
    "难度分析",
    "备考",
    "复习",
    "报考条件",
    "考试流程",
    "专业分类目录",
    "政策解读",
    "预测",
    "预计",
    "报名人数",
    "竞争比",
    "真题",
    "模拟题",
    "题库",
    "考试时间",
)
_EXAM_CONTENT_PATH_RE = re.compile(
    r"/(?:shenlun|xingce|zhenti|moniti|beikao|peixun|kecheng|"
    r"jiaofu|kaoshi|chengji|mianshi|gkzy|gkkslc|gkbktj|gkmsjq|"
    r"gkmstz|gktjkc|gktj|gklymd)(?:/|$)",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def is_navigation_discovery_link(url: str, title: str = "") -> bool:
    """Return whether a third-party link is clearly site navigation.

    This is a quality filter for the private lead pool only.  It never proves
    that another link is an official vacancy, and it intentionally leaves
    ordinary article/listing pages untouched for later first-party recovery.
    """

    normalized_title = re.sub(r"\s+", " ", str(title or "").strip())
    if _NAVIGATION_TITLE_RE.fullmatch(normalized_title):
        return True
    lowered_title = normalized_title.casefold()
    if any(marker.casefold() in lowered_title for marker in _NAVIGATION_TITLE_MARKERS):
        return True
    if "课程" in lowered_title and any(
        marker in lowered_title for marker in ("考试", "培训", "辅导")
    ):
        return True
    parsed = urlsplit(str(url or "").strip())
    path = parsed.path or "/"
    return bool(_NAVIGATION_PATH_RE.search(path))


def is_non_vacancy_discovery_link(url: str, title: str = "") -> bool:
    """Return whether a clue is an exam/content article, not a vacancy.

    This is deliberately stricter than the navigation filter.  It is applied
    only to private discovery leads and never to first-party official pages.
    Official notices such as ``考试录用公务员职位表`` remain eligible because
    they contain vacancy markers and none of the explicit non-vacancy markers.
    """

    normalized_title = re.sub(r"\s+", " ", str(title or "").strip())
    lowered_title = normalized_title.casefold()
    if any(marker.casefold() in lowered_title for marker in _NON_VACANCY_TITLE_MARKERS):
        return True
    parsed = urlsplit(str(url or "").strip())
    return bool(_NON_VACANCY_PATH_RE.search(parsed.path or "/"))


def discovery_link_quality_category(url: str, title: str = "") -> str | None:
    """Classify a private third-party clue without treating it as evidence.

    ``None`` intentionally includes recruitment announcements and position-table
    roundups. Those clues remain eligible for a later first-party URL recovery;
    they still cannot be published until an official page or attachment is
    fetched and every row-level gate passes.
    """

    normalized_title = re.sub(r"\s+", " ", str(title or "").strip())
    lowered_title = normalized_title.casefold()
    parsed = urlsplit(str(url or "").strip())
    path = parsed.path or "/"
    if is_navigation_discovery_link(url, title):
        return "navigation"
    if any(marker.casefold() in lowered_title for marker in _RESULT_OR_PUBLICITY_MARKERS):
        return "result_or_publicity"
    if any(marker.casefold() in lowered_title for marker in _TRAINING_OR_AD_MARKERS):
        return "training_or_ad"
    if _EXAM_CONTENT_PATH_RE.search(path):
        return "exam_content"
    years = {int(value) for value in _YEAR_RE.findall(f"{normalized_title} {path}")}
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    if years and max(years) < current_year:
        return "historical"
    return None


def discovery_link_quality_reason(url: str, title: str = "") -> str | None:
    """Return a stable private-filter reason for a rejected discovery clue."""

    category = discovery_link_quality_category(url, title)
    if category == "navigation":
        return "navigation_link_v1"
    if category:
        return "non_vacancy_content_v1"
    return None


def reconcile_navigation_discovery_leads(
    database: Any,
    *,
    limit: int = 50_000,
) -> dict[str, int]:
    """Reject only obvious navigation clues while retaining their evidence.

    Older scanners persisted category homepages and application-entry pages as
    ``candidate`` or ``official_url_found``.  They must not inflate the
    official-URL funnel.  The rows remain in the database and receive a normal
    lead status event, so this is reversible through an explicit review.
    """

    scanned = rejected = skipped = errors = 0
    rows = database.list_candidate_leads(limit=max(1, min(int(limit), 50_000)))
    for row in rows:
        scanned += 1
        status = str(row.get("verification_status") or "candidate")
        if status in {"rejected", "expired", "published"}:
            skipped += 1
            continue
        filter_reason = discovery_link_quality_reason(
            str(row.get("lead_url") or ""), str(row.get("title") or "")
        )
        if not filter_reason:
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        category = discovery_link_quality_category(
            str(row.get("lead_url") or ""), str(row.get("title") or "")
        )
        metadata["quality_filter"] = {
            "rule": filter_reason,
            "category": category or "unknown",
            "reason": (
                "第三方导航/报名入口或考试内容文章不是岗位公告或职位表，"
                "保留线索但不进入官方 URL 转化。"
            ),
        }
        try:
            database.update_candidate_lead(
                int(row["id"]),
                {
                    "verification_status": "rejected",
                    "verification_note": (
                        "自动质量过滤：该链接是第三方导航、报名入口或考试内容文章，"
                        "不是可追溯的岗位公告/职位表。原始线索保留，待有实际公告链接时重新登记。"
                    ),
                    "metadata": metadata,
                },
            )
            rejected += 1
        except (KeyError, TypeError, ValueError):
            errors += 1
    return {
        "scanned": scanned,
        "rejected": rejected,
        "skipped": skipped,
        "errors": errors,
    }


def load_discovery_source_registry(
    path: Path = DISCOVERY_SOURCE_REGISTRY_PATH,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read discovery source registry: {error}") from error
    try:
        return validate_discovery_source_registry(payload)
    except ContractValidationError as error:
        raise ValueError(f"Invalid discovery source registry: {error}") from error


def discovery_source_summary(
    registry: dict[str, Any],
    *,
    lead_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return counts that explain discovery coverage without exposing leads."""
    sources = list(registry.get("sources", []))
    rows = lead_rows or []
    status_names = (
        "candidate",
        "official_url_found",
        "official_content_verified",
        "need_review",
        "published",
        "rejected",
        "expired",
    )
    by_source: dict[str, dict[str, int]] = {
        str(source["id"]): {
            "leads": 0,
            **{status: 0 for status in status_names},
        }
        for source in sources
    }
    unassigned = 0
    for lead in rows:
        source_id = str(lead.get("discovery_source_id") or "").strip()
        if source_id not in by_source:
            unassigned += 1
            continue
        bucket = by_source[source_id]
        bucket["leads"] += 1
        status = str(lead.get("verification_status") or "")
        if status in bucket:
            bucket[status] += 1
    return {
        "registered_sources": len(sources),
        "active_sources": sum(source.get("status") in {"registered", "checked"} for source in sources),
        "access_limited_sources": sum(source.get("status") == "access_limited" for source in sources),
        "lead_count": len(rows),
        "unassigned_lead_count": unassigned,
        "sources": [
            {
                "id": source["id"],
                "name": source["name"],
                "source_type": source["source_type"],
                "status": source["status"],
                "last_checked_on": source["last_checked_on"],
                "lead_counts": by_source[str(source["id"])],
            }
            for source in sources
        ],
    }


def discovery_source_rows(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the private registry rows for an administrator."""
    return [dict(source) for source in registry.get("sources", [])]


def normalize_lead_url(url: str) -> str:
    """Normalize tracking noise for deduplication, while retaining the source URL."""
    parsed = urlsplit(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return str(url).strip()
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"from", "source", "share", "spm"}
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(sorted(query)),
            "",
        )
    )


def lead_fingerprint(
    lead_url: str,
    *,
    title: str = "",
    employer_hint: str = "",
    identity_hint: str = "",
) -> str:
    """Create a stable URL key for idempotent private lead ingestion.

    Titles on aggregators are often edited after first publication.  The
    normalized article URL is the durable identity; title and employer remain
    useful fields on the first captured row, but must not create duplicates.
    """
    del title, employer_hint
    identity = normalize_lead_url(lead_url)
    if str(identity_hint).strip():
        identity = f"{identity}\x00{str(identity_hint).strip()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def official_domain_assessment(
    official_url: str,
    source_records: list[dict[str, Any]],
    *,
    official_source_id: str | None = None,
) -> dict[str, Any]:
    """Assess an official URL against registered source hosts without network I/O."""
    parsed = urlsplit(str(official_url).strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return {"status": "unverified", "host": "", "source_id": official_source_id}
    source = (
        next(
            (item for item in source_records if str(item.get("id")) == str(official_source_id)),
            None,
        )
        if official_source_id
        else None
    )
    if source is not None:
        allowed = {
            str(value).lower().rstrip(".")
            for value in source.get("config", {}).get("allowed_hosts", [])
        }
        homepage = urlsplit(str(source.get("homepage_url") or "")).hostname
        if homepage:
            allowed.add(homepage.lower().rstrip("."))
        matches = any(host == item or host.endswith("." + item) for item in allowed)
        return {
            "status": "registered_source_match" if matches else "source_domain_mismatch",
            "host": host,
            "source_id": official_source_id,
        }
    return {
        "status": "manual_review_required",
        "host": host,
        "source_id": official_source_id,
    }


def official_candidate_quality(url: str, *, label: str = "") -> str:
    """Classify whether a recovered URL can point to a vacancy document."""

    raw = str(url or "").strip()
    if not _is_http_url(raw):
        return "invalid_url"
    parsed = urlsplit(raw)
    path = (parsed.path or "").strip().rstrip("/").casefold()
    if not path or path in {"/index", "/index.html", "/index.htm"}:
        return "generic_homepage"
    if _OFFICIAL_NON_VACANCY_PATH_RE.search(path):
        return "non_vacancy_entry"
    if _NON_VACANCY_LINK_LABEL_RE.search(str(label or "")):
        return "non_vacancy_entry"
    return "job_detail_or_notice"


def resolve_official_url_candidates(
    recovery: dict[str, Any],
    source_records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Bind third-party extracted links to registered first-party sources.

    This is a URL-resolution step only. It never fetches the candidate or
    changes a lead's verification status; the caller may safely persist the
    returned candidates as private evidence and schedule a first-party fetch.
    """

    if not isinstance(recovery, dict):
        return []
    raw_candidates: list[dict[str, Any]] = []
    for kind, values in (
        ("official_page", recovery.get("official_link_candidates")),
        ("official_attachment", recovery.get("attachment_candidates")),
    ):
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                raw_candidates.append({**value, "candidate_kind": kind})
    resolved: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        url = normalize_recovered_url(item.get("url"))
        if not is_recruitment_official_url(url):
            continue
        matched_registered_source = False
        for source in source_records:
            source_id = str(source.get("id") or "").strip()
            if not source_id:
                continue
            assessment = official_domain_assessment(
                url,
                source_records,
                official_source_id=source_id,
            )
            if assessment.get("status") != "registered_source_match":
                continue
            matched_registered_source = True
            key = (url, source_id)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                {
                    "url": url,
                    "label": str(item.get("label") or "")[:200],
                    "host": str(assessment.get("host") or ""),
                    "official_source_id": source_id,
                    "domain_status": "registered_source_match",
                    "candidate_quality": official_candidate_quality(
                        url, label=str(item.get("label") or "")
                    ),
                    "candidate_kind": str(item.get("candidate_kind") or "official_page"),
                }
            )
        # An unregistered government or university host is still a useful
        # private URL candidate. Keep it explicitly unbound so it cannot count
        # as a verified source URL or pass the publication gate.
        if not matched_registered_source:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            if (
                (host.endswith(".gov.cn") or host.endswith(".edu.cn"))
                and not is_non_recruitment_official_host(host)
            ):
                key = (url, "")
                if key not in seen:
                    seen.add(key)
                    resolved.append(
                        {
                            "url": url,
                            "label": str(item.get("label") or "")[:200],
                            "host": host,
                            "official_source_id": "",
                            "domain_status": "manual_review_required",
                            "candidate_quality": official_candidate_quality(
                                url, label=str(item.get("label") or "")
                            ),
                            "candidate_kind": str(item.get("candidate_kind") or "official_page"),
                        }
                    )
    resolved.sort(
        key=lambda item: (
            item.get("candidate_quality") != "job_detail_or_notice",
            item.get("url", ""),
            item.get("official_source_id", ""),
        )
    )
    return resolved


def recover_persisted_official_url_candidates(
    database: Any,
    *,
    source_records: list[dict[str, Any]] | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Replay already captured recovery links into the private URL stage.

    Discovery scans persist the article's extracted links before an official
    source verifier is run.  A deployment that gains a newer source registry
    or resolver must be able to replay those records without fetching the
    third-party pages again.  This function is intentionally limited to the
    ``candidate``/``need_review`` -> ``official_url_found`` transition; it
    never verifies official content or publishes a job.
    """

    records = source_records or database.list_sources(enabled_only=False)
    rows = database.list_candidate_leads(limit=max(1, min(int(limit), 50_000)))
    counts = {
        "scanned": 0,
        "resolved": 0,
        "updated": 0,
        "already_resolved": 0,
        "deferred_generic_homepage": 0,
        "unresolved": 0,
        "skipped": 0,
        "errors": 0,
        "resolution_reasons": {},
    }

    def record_resolution(
        row: dict[str, Any],
        metadata: dict[str, Any],
        recovery: dict[str, Any],
        *,
        status: str,
        reason: str,
        resolved_count: int = 0,
    ) -> None:
        """Persist a per-lead replay outcome without changing publication state."""

        reasons = counts["resolution_reasons"]
        reasons[reason] = int(reasons.get(reason, 0)) + 1
        next_recovery = dict(recovery)
        next_recovery["recovery_resolution"] = {
            "status": status,
            "reason": reason,
            "resolved_count": int(resolved_count),
            "recorded_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        }
        next_metadata = dict(metadata)
        next_metadata["official_recovery"] = next_recovery
        try:
            database.update_candidate_lead(
                int(row["id"]), {"metadata": next_metadata}
            )
        except (KeyError, TypeError, ValueError):
            counts["errors"] += 1

    for row in rows:
        counts["scanned"] += 1
        status = str(row.get("verification_status") or "candidate")
        if status == "official_url_found":
            metadata = row.get("metadata")
            recovery = metadata.get("official_recovery") if isinstance(metadata, dict) else None
            if isinstance(recovery, dict):
                resolved = resolve_official_url_candidates(recovery, records)
                current_url = str(row.get("official_url") or "").strip()
                current_matches = [item for item in resolved if item["url"] == current_url]
                if current_matches and not any(
                    item["candidate_quality"] == "job_detail_or_notice"
                    for item in current_matches
                ):
                    winner = next(
                        (
                            item for item in resolved
                            if item["candidate_quality"] == "job_detail_or_notice"
                        ),
                        None,
                    )
                    next_recovery = dict(recovery)
                    next_recovery["resolved_official_url_candidates"] = resolved
                    next_metadata = {**metadata, "official_recovery": next_recovery}
                    try:
                        database.update_candidate_lead(
                            int(row["id"]),
                            {
                                "official_url": winner["url"] if winner else None,
                                "official_source_id": (
                                    winner.get("official_source_id") or None
                                ) if winner else None,
                                "official_domain_status": (
                                    winner["domain_status"] if winner else "unverified"
                                ),
                                "verification_status": (
                                    "official_url_found" if winner else "need_review"
                                ),
                                "verification_note": (
                                    "原官方 URL 候选实际为报名/考试材料，已按附件标签重新分类；"
                                    "替代岗位表仍须独立核验官方内容与当前性。"
                                ),
                                "metadata": next_metadata,
                            },
                        )
                    except (KeyError, TypeError, ValueError):
                        counts["errors"] += 1
                    else:
                        counts["updated"] += 1
                    continue
        if status not in {"candidate", "need_review"}:
            counts["skipped"] += 1
            continue
        if discovery_link_quality_reason(
            str(row.get("lead_url") or ""), str(row.get("title") or "")
        ):
            counts["skipped"] += 1
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        recovery = metadata.get("official_recovery")
        if not isinstance(recovery, dict):
            counts["unresolved"] += 1
            record_resolution(
                row,
                metadata,
                {},
                status="unresolved",
                reason="no_recovery_evidence",
            )
            continue
        resolved = resolve_official_url_candidates(recovery, records)
        if not resolved:
            counts["unresolved"] += 1
            raw_candidates = []
            for key in ("official_link_candidates", "attachment_candidates"):
                values = recovery.get(key)
                if isinstance(values, list):
                    raw_candidates.extend(item for item in values if isinstance(item, dict))
            record_resolution(
                row,
                metadata,
                recovery,
                status="unresolved",
                reason=(
                    "no_candidate_links"
                    if not raw_candidates
                    else "no_registered_or_government_candidate"
                ),
            )
            continue
        counts["resolved"] += len(resolved)
        if str(row.get("official_url") or "").strip():
            counts["already_resolved"] += 1
            record_resolution(
                row,
                metadata,
                recovery,
                status="already_resolved",
                reason="already_has_official_url",
                resolved_count=len(resolved),
            )
            continue
        if resolved[0].get("candidate_quality") != "job_detail_or_notice":
            counts["deferred_generic_homepage"] += 1
            next_recovery = dict(recovery)
            next_recovery["resolved_official_url_candidates"] = resolved
            next_recovery["recovery_replayed_at"] = datetime.now(
                ZoneInfo("Asia/Shanghai")
            ).isoformat()
            record_resolution(
                row,
                metadata,
                next_recovery,
                status="deferred",
                reason=(
                    "non_vacancy_entry_only"
                    if resolved[0].get("candidate_quality") == "non_vacancy_entry"
                    else "generic_homepage_only"
                ),
                resolved_count=len(resolved),
            )
            continue
        winner = resolved[0]
        next_recovery = dict(recovery)
        next_recovery["resolved_official_url_candidates"] = resolved
        next_recovery["recovery_replayed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        try:
            database.update_candidate_lead(
                int(row["id"]),
                {
                    "official_url": winner["url"],
                    "official_source_id": winner.get("official_source_id") or None,
                    "official_domain_status": winner["domain_status"],
                    "verification_status": "official_url_found",
                    "verification_note": (
                        "已从已保存的第三方恢复证据解析出官方 URL 候选；"
                        "来源主体仍需登记的候选会保持待复核状态；"
                        "尚未核验官方内容字段，不能发布。"
                    ),
                    "metadata": {
                        **metadata,
                        "official_recovery": {
                            **next_recovery,
                            "recovery_resolution": {
                                "status": "resolved",
                                "reason": "job_level_candidate",
                                "resolved_count": len(resolved),
                                "recorded_at": datetime.now(
                                    ZoneInfo("Asia/Shanghai")
                                ).isoformat(),
                            },
                        },
                    },
                },
            )
            counts["updated"] += 1
            reasons = counts["resolution_reasons"]
            reasons["job_level_candidate"] = int(
                reasons.get("job_level_candidate", 0)
            ) + 1
        except (KeyError, TypeError, ValueError):
            counts["errors"] += 1
    return counts


def discovery_funnel(
    lead_rows: list[dict[str, Any]],
    registry: dict[str, Any],
    *,
    jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate an auditable lead funnel; no count is presented as a job count."""
    statuses = {
        "candidate": 0,
        "official_url_found": 0,
        "official_content_verified": 0,
        "need_review": 0,
        "published": 0,
        "rejected": 0,
        "expired": 0,
    }
    domains: dict[str, int] = {}
    fingerprints: set[str] = set()
    attributed = 0
    for row in lead_rows:
        status = str(row.get("verification_status") or "")
        if status in statuses:
            statuses[status] += 1
        domain_status = str(row.get("official_domain_status") or "unverified")
        domains[domain_status] = domains.get(domain_status, 0) + 1
        fingerprint = str(row.get("lead_fingerprint") or "").strip()
        if fingerprint:
            fingerprints.add(fingerprint)
        if str(row.get("discovery_source_id") or "").strip():
            attributed += 1
    total = len(lead_rows)

    def rate(value: int, denominator: int = total) -> float:
        return round(value / denominator, 4) if denominator else 0.0

    public_jobs = list(jobs or [])
    current_public_jobs = [job for job in public_jobs if is_public_job(job)]
    current_domestic_jobs = [job for job in current_public_jobs if is_domestic_job(job)]
    growth_metrics = _growth_metrics(lead_rows, current_domestic_jobs)
    grouped_leads: dict[str, list[dict[str, Any]]] = {}
    for row in lead_rows:
        source_id = str(row.get("discovery_source_id") or "unattributed").strip()
        grouped_leads.setdefault(source_id or "unattributed", []).append(row)
    source_growth_metrics = {
        source_id: _growth_metrics(rows, None)
        for source_id, rows in sorted(grouped_leads.items())
    }

    return {
        "lead_total": total,
        "unique_lead_fingerprints": len(fingerprints),
        "duplicate_lead_rows": max(0, total - len(fingerprints)),
        "status_counts": statuses,
        "domain_status_counts": domains,
        "attributed_leads": attributed,
        "unattributed_leads": total - attributed,
        "rates": {
            "official_url_found": rate(
                statuses["official_url_found"]
                + statuses["official_content_verified"]
                + statuses["published"]
            ),
            "official_content_verified": rate(
                statuses["official_content_verified"] + statuses["published"]
            ),
            "published": rate(statuses["published"]),
            "source_attribution": rate(attributed),
        },
        "registered_discovery_sources": len(registry.get("sources", [])),
        "growth_metrics": growth_metrics,
        "source_growth_metrics": source_growth_metrics,
        "policy": "线索统计不等于公开岗位统计；第三方原文不会进入学生端。",
    }


def _growth_metrics(
    lead_rows: list[dict[str, Any]],
    current_domestic_jobs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return the six explicit expansion metrics used by the admin plan.

    Every count is derived from a persisted field or an explicitly recorded
    verification marker. No title keyword inference is allowed here: a clue
    without an official URL, complete row evidence, or a precise-major marker
    remains in its earlier funnel stage.
    """

    official_url_statuses = {
        "official_url_found",
        "official_content_verified",
        "published",
    }
    verified_statuses = {"official_content_verified", "published"}
    official_url_count = sum(
        1
        for row in lead_rows
        if str(row.get("verification_status") or "") in official_url_statuses
        and _lead_has_bound_official_url(row)
    )
    # A URL extracted from a third-party page is useful expansion evidence
    # even when its host is not yet registered in the first-party source
    # registry.  Count it separately from ``official_url_count`` so the
    # operator can measure URL recovery without treating an unbound link as
    # verified official evidence.
    raw_official_url_candidate_count = sum(
        1 for row in lead_rows if _lead_has_private_official_url_candidate(row)
    )
    official_url_candidate_count = sum(
        1 for row in lead_rows if _lead_has_actionable_official_url_candidate(row)
    )
    official_content_verified_count = sum(
        1
        for row in lead_rows
        if str(row.get("verification_status") or "") in verified_statuses
        and _lead_has_bound_official_url(row)
    )
    field_complete_count = sum(
        1
        for row in lead_rows
        if str(row.get("verification_status") or "") in verified_statuses
        and _lead_fields_complete(row)
    )
    precise_major_pass_count = sum(
        1
        for row in lead_rows
        if str(row.get("verification_status") or "") in verified_statuses
        and _lead_fields_complete(row)
        and _lead_precise_major_passed(row)
    )
    similar_major_pass_count = sum(
        1
        for row in lead_rows
        if str(row.get("verification_status") or "") in verified_statuses
        and _lead_fields_complete(row)
        and _lead_major_match_level(row) == "similar"
    )
    broad_major_pass_count = sum(
        1
        for row in lead_rows
        if str(row.get("verification_status") or "") in verified_statuses
        and _lead_fields_complete(row)
        and _lead_major_match_level(row) == "broad"
    )
    # Stable five-stage projection used by the admin dashboard and reports.
    # ``None`` (for current published jobs when no job snapshot was supplied)
    # is intentionally distinct from zero.
    five_stage_metrics = {
        "lead_count": len(lead_rows),
        "official_url_count": official_url_count,
        "field_complete_count": field_complete_count,
        "precise_major_pass_count": precise_major_pass_count,
        "current_published_count": (
            len(current_domestic_jobs) if current_domestic_jobs is not None else None
        ),
    }
    return {
        "metric_contract_version": 3,
        "five_stage_metrics": five_stage_metrics,
        "lead_count": len(lead_rows),
        "official_url_count": official_url_count,
        "official_url_candidate_count": official_url_candidate_count,
        "raw_official_url_candidate_count": raw_official_url_candidate_count,
        "official_content_verified_count": official_content_verified_count,
        "field_complete_count": field_complete_count,
        "precise_major_pass_count": precise_major_pass_count,
        "current_published_count": (
            len(current_domestic_jobs) if current_domestic_jobs is not None else None
        ),
        "rates": {
            "official_url_from_lead": _rate(official_url_count, len(lead_rows)),
            "official_url_candidate_from_lead": _rate(
                official_url_candidate_count, len(lead_rows)
            ),
            "official_content_from_lead": _rate(
                official_content_verified_count, len(lead_rows)
            ),
            "field_complete_from_verified": _rate(
                field_complete_count, official_content_verified_count
            ),
            "precise_major_from_field_complete": _rate(
                precise_major_pass_count, field_complete_count
            ),
            "similar_major_from_field_complete": _rate(
                similar_major_pass_count, field_complete_count
            ),
            "broad_major_from_field_complete": _rate(
                broad_major_pass_count, field_complete_count
            ),
        },
        "scope_note": (
            "lead_count 是去重后的第三方线索行数；official_url_count 只计有效 HTTP(S)"
            "且已通过登记来源边界、线索不是考试/报名内容、链接不是通用首页或登录入口的"
            "岗位级官方 URL；field_complete_count 只计已核验且具备岗位、"
            "单位、专业、学历、地点、截止日期和岗位级标题/范围证据的线索；"
            "precise_major_pass_count 由 evaluate_student_publication() 重新判定四个毕业专业，"
            "similar_major_pass_count 只统计同一官方岗位证据中明确出现相近地学方向的私有候选，"
            "broad_major_pass_count 只统计公共招录代码/大类/不限专业规则；二者均不改变企业岗位"
            "精确专业发布门禁；"
            "不信任旧的 exact_four_names 标记；current_published_count 只计当前中国大陆学生端"
            "公开岗位。official_url_candidate_count 只计未拒收线索中的岗位级私有 URL"
            "候选；raw_official_url_candidate_count 保留原始提取量（含通用首页与被拒线索）。"
            "二者都不能当作已核验官方 URL 或岗位数。"
            "当前发布数是全站实时快照，不属于第三方线索同一队列，"
            "不能除以线索精确专业通过数得出转化率。任何阶段都不能由标题关键词推断。"
        ),
    }


def _lead_has_private_official_url_candidate(row: dict[str, Any]) -> bool:
    """Return whether replay persisted at least one private URL candidate.

    The replay step writes candidates under ``official_recovery`` before any
    first-party fetch.  Requiring a valid HTTP(S) URL and excluding filing
    hosts keeps this metric useful while preserving the publication boundary.
    """

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return False
    recovery = metadata.get("official_recovery")
    if not isinstance(recovery, dict):
        return False
    candidates = recovery.get("resolved_official_url_candidates")
    if not isinstance(candidates, list):
        return False
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if is_recruitment_official_url(url):
            return True
    return False


def _lead_has_actionable_official_url_candidate(row: dict[str, Any]) -> bool:
    if str(row.get("verification_status") or "") not in {
        "candidate", "need_review", "official_url_found"
    }:
        return False
    if discovery_link_quality_reason(
        str(row.get("lead_url") or ""), str(row.get("title") or "")
    ):
        return False
    metadata = row.get("metadata")
    recovery = metadata.get("official_recovery") if isinstance(metadata, dict) else None
    candidates = (
        recovery.get("resolved_official_url_candidates")
        if isinstance(recovery, dict)
        else None
    )
    return isinstance(candidates, list) and any(
        isinstance(item, dict)
        and is_recruitment_official_url(item.get("url"))
        and official_candidate_quality(
            str(item.get("url") or ""), label=str(item.get("label") or "")
        ) == "job_detail_or_notice"
        for item in candidates
    )


def _is_http_url(value: object) -> bool:
    text = str(value or "").strip()
    if re.search(r"[\s<>\"']", text):
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_recruitment_official_url(value: object) -> bool:
    """Exclude statutory footer filing endpoints from the official URL funnel."""
    if not _is_http_url(value):
        return False
    host = (urlsplit(str(value).strip()).hostname or "").lower().rstrip(".")
    return not is_non_recruitment_official_host(host)


def _lead_fields_complete(row: dict[str, Any]) -> bool:
    if not _lead_has_bound_official_url(row):
        return False
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return False
    evidence = metadata.get("field_evidence")
    if not isinstance(evidence, dict):
        return False
    title = str(metadata.get("title") or row.get("title") or "").strip()
    normalized_title = title.casefold()
    evidence_title = any(
        (
            evidence_value := str(evidence.get(key) or "").strip().casefold()
        )
        and (evidence_value == normalized_title
             or evidence_value in normalized_title
             or normalized_title in evidence_value)
        for key in ("岗位", "岗位名称", "招聘岗位", "职位", "职位名称", "title")
    )
    evidence_scope = str(evidence.get("evidence_scope") or "").strip()
    if not title or not evidence_title or evidence_scope not in JOB_LEVEL_EVIDENCE_SCOPES:
        return False
    major = any(
        str(evidence.get(key) or "").strip()
        for key in ("专业范围", "专业要求", "专业", "major", "majors")
    )
    degree = any(
        str(evidence.get(key) or "").strip()
        for key in ("学历要求", "学历", "degree", "education")
    )
    employer = str(metadata.get("employer") or row.get("employer_hint") or "").strip()
    location = str(
        metadata.get("location")
        or row.get("location_hint")
        or row.get("province_hint")
        or ""
    ).strip()
    deadline = str(metadata.get("deadline_date") or "").strip()
    return bool(major and degree and employer and location and deadline)


def _lead_precise_major_passed(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return False
    evidence = metadata.get("field_evidence")
    if not isinstance(evidence, dict):
        return False
    # Re-evaluate the actual row evidence. A lead may contain a legacy marker,
    # but that marker is only a claim and must never promote a broad phrase
    # such as “地质类” to one of the four graduation majors.
    job = {
        "title": str(metadata.get("title") or row.get("title") or "").strip(),
        "employer": str(metadata.get("employer") or row.get("employer_hint") or "").strip(),
        "location": str(
            metadata.get("location")
            or row.get("location_hint")
            or row.get("province_hint")
            or ""
        ).strip(),
        "country_or_region": str(metadata.get("country_or_region") or "中国大陆").strip(),
        "deadline_date": str(metadata.get("deadline_date") or "").strip(),
        "category": str(metadata.get("category") or "企业校园招聘").strip(),
        "source_id": str(metadata.get("source_id") or row.get("official_source_id") or "").strip(),
        "official_evidence_url": str(row.get("official_url") or "").strip(),
        "field_evidence": evidence,
    }
    decision = evaluate_student_publication(job)
    return (
        decision.status == PUBLICATION_STUDENT_ELIGIBLE
        and decision.major_match_mode == "exact_four_names"
    )


def _lead_major_match_level(row: dict[str, Any]) -> str:
    """Return the additive exact/similar/broad projection for one verified lead."""

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return "pending"
    evidence = metadata.get("field_evidence")
    if not isinstance(evidence, dict):
        return "pending"
    job = {
        "title": str(metadata.get("title") or row.get("title") or "").strip(),
        "employer": str(metadata.get("employer") or row.get("employer_hint") or "").strip(),
        "location": str(
            metadata.get("location")
            or row.get("location_hint")
            or row.get("province_hint")
            or ""
        ).strip(),
        "country_or_region": str(metadata.get("country_or_region") or "中国大陆").strip(),
        "deadline_date": str(metadata.get("deadline_date") or "").strip(),
        "category": str(metadata.get("category") or "企业校园招聘").strip(),
        "source_id": str(metadata.get("source_id") or row.get("official_source_id") or "").strip(),
        "official_evidence_url": str(row.get("official_url") or "").strip(),
        "field_evidence": evidence,
    }
    return str(classify_major_match(job).get("level") or "pending")


def _lead_has_bound_official_url(row: dict[str, Any]) -> bool:
    """Return whether a lead URL has passed the first-party source boundary."""

    if not is_recruitment_official_url(row.get("official_url")):
        return False
    if official_candidate_quality(str(row.get("official_url") or "")) != "job_detail_or_notice":
        return False
    metadata = row.get("metadata")
    recovery = metadata.get("official_recovery") if isinstance(metadata, dict) else None
    if isinstance(recovery, dict):
        candidates = []
        for field in (
            "resolved_official_url_candidates",
            "official_link_candidates",
            "attachment_candidates",
        ):
            values = recovery.get(field)
            if isinstance(values, list):
                candidates.extend(item for item in values if isinstance(item, dict))
        matching = [
            item for item in candidates
            if str(item.get("url") or "") == str(row.get("official_url") or "")
        ]
        if matching and not any(
            official_candidate_quality(
                str(item.get("url") or ""), label=str(item.get("label") or "")
            ) == "job_detail_or_notice"
            for item in matching
        ):
            return False
    if discovery_link_quality_reason(
        str(row.get("lead_url") or ""), str(row.get("title") or "")
    ):
        return False
    return str(row.get("official_domain_status") or "").strip() in {
        "registered_source_match",
        "manual_review_approved",
    }


def _rate(value: int, denominator: int) -> float:
    return round(value / denominator, 4) if denominator else 0.0
