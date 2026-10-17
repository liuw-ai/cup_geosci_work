"""Parse the public Zhaopin campus job-detail document.

The campus detail route is server-rendered, but the authoritative fields are
carried in ``window.__INITIAL_DATA__``.  Keeping this parser independent from
the browser runner lets HTTP fixtures, Playwright captures, and future annual
campaigns share the same evidence rules.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from job_hub.locations import extract_location_hint
from job_hub.matching import clean_text, extract_deadline, extract_published_date, parse_date_value


class ZhaopinDetailError(ValueError):
    """Raised when a public detail document cannot prove one job record."""


_INITIAL_DATA_RE = re.compile(r"window\.__INITIAL_DATA__\s*=\s*", re.IGNORECASE)
_REQUIREMENT_VALUE_BOUNDARY = (
    r"(?=[；;。\n]|"
    r"\s+\d+\s*[.、．]\s*"
    r"(?:学历|教育程度|专业|外语|英语|能力|任职|岗位|其他|工作地点|"
    r"招聘人数|资格|工作经历|经验)(?:要求|条件)?\s*[:：]|$)"
)
_DEGREE_REQUIREMENT_RE = re.compile(
    r"(?:学历要求|学历条件|最低学历|教育程度)\s*[:：]\s*"
    r"(?P<value>[^；;。\n]{1,80}?)" + _REQUIREMENT_VALUE_BOUNDARY,
    re.IGNORECASE,
)
_MAJOR_REQUIREMENT_RE = re.compile(
    r"(?:专业要求|专业条件|所学专业|专业范围)\s*[:：]\s*"
    r"(?P<value>[^；;。\n]{1,300}?)" + _REQUIREMENT_VALUE_BOUNDARY,
    re.IGNORECASE,
)
_REQUIREMENT_SECTION_RE = re.compile(r"(?:任职要求|岗位要求)\s*[:：]?", re.IGNORECASE)
_NUMBERED_REQUIREMENT_RE = re.compile(
    r"(?:^|[；;。])\s*\d+\s*[.、．]\s*(?P<value>[^；;。\n]{1,300})"
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return parse_date_value(text) or extract_deadline(text)


def _detail_location(detail: dict[str, Any], job_desc: str) -> str:
    """Preserve the city and street address exposed by one official detail.

    Zhaopin often provides a terse street address in ``workAddress`` and the
    city separately in ``positionWorkCity``.  Preferring the street address
    alone makes an otherwise domestic role impossible to classify.  All
    values below belong to the same detail payload; no employer-level place
    inference is involved.
    """

    city = _text(
        detail.get("positionWorkCity")
        or detail.get("positionCity")
        or detail.get("positionCityDistrict")
    )
    if city in {"全国", "全国项目地", "全国范围", "全国各地"}:
        return city

    values = (
        city,
        _text(detail.get("positionCityDistrict")),
        _text(detail.get("workAddress")),
        _text(extract_location_hint(job_desc)),
    )
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return " ".join(unique)


def extract_zhaopin_degree_requirement(job_desc: str, fallback: str) -> str:
    """Preserve a detail's degree floor instead of trusting an ATS enum alone.

    Zhaopin detail payloads commonly expose ``education=本科`` while the
    authoritative requirement block says ``本科及以上``.  Keeping the
    bounded phrase from that same detail block lets the publication gate
    correctly include higher-degree students without inferring eligibility
    from an unrelated announcement paragraph.
    """

    match = _DEGREE_REQUIREMENT_RE.search(job_desc)
    if match:
        value = _text(match.group("value"))
        if value:
            return value
    return fallback


def extract_zhaopin_major_requirement(job_desc: str) -> str:
    """Extract only the detail's job-level major condition.

    A job description commonly mentions geology, oil and gas, or geophysics
    in its duties.  Those words do not prove that a Geoscience student can
    apply.  The public evidence field must therefore be the explicit
    ``专业要求`` clause, not the entire description.  A small subset of the
    official CNOOC templates uses numbered conditions without a label; for
    those, accept only the major-like item after the requirement-section
    heading.
    """

    # A legacy database row may have been persisted as
    # ``专业要求：<entire jobDesc>``.  Its first regex match is merely that
    # synthetic wrapper around duties.  Continue to the bounded requirement
    # inside the same official detail instead of accepting that wrapper.
    for match in _MAJOR_REQUIREMENT_RE.finditer(job_desc):
        value = _text(match.group("value"))
        if value and not any(
            marker in value
            for marker in (
                "岗位职责",
                "任职要求",
                "学历要求",
                "外语要求",
                "英语要求",
                "工作地点",
                "能力/素质",
            )
        ):
            return value

    section = _REQUIREMENT_SECTION_RE.search(job_desc)
    if not section:
        return ""
    requirement_text = job_desc[section.end() :]
    for match in _NUMBERED_REQUIREMENT_RE.finditer(requirement_text):
        value = _text(match.group("value"))
        if not value or any(
            marker in value for marker in ("学历", "外语", "英语", "工作地点", "岗位职责")
        ):
            continue
        # The unlabelled fallback is deliberately narrow.  It must still say
        # that it is a discipline/major condition rather than merely mention
        # a professional activity in a numbered duty.
        if re.search(r"(?:相关|相近|所学)?专业(?:$|[，、,;；])", value):
            return value
    return ""


def _extract_initial_data(html: str) -> dict[str, Any]:
    match = _INITIAL_DATA_RE.search(html)
    if not match:
        raise ZhaopinDetailError("official detail is missing window.__INITIAL_DATA__")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(html[match.end() :].lstrip())
    except json.JSONDecodeError as error:
        raise ZhaopinDetailError("official detail INITIAL_DATA is not valid JSON") from error
    if not isinstance(value, dict):
        raise ZhaopinDetailError("official detail INITIAL_DATA is not an object")
    return value


def parse_zhaopin_detail_html(
    html: str,
    *,
    detail_url: str,
    expected_job_number: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> dict[str, Any]:
    """Return normalized, job-level evidence from one official detail page.

    A missing field or an ID mismatch raises instead of returning a partial
    row.  Callers should put that URL in a review queue, never interpret the
    failure as a source with no vacancies.
    """

    parsed = urlparse(detail_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ZhaopinDetailError("detail URL must be an HTTP(S) URL")
    hosts = {str(host).strip().lower().rstrip(".") for host in (allowed_hosts or {"xiaoyuan.zhaopin.com"})}
    if parsed.hostname.lower().rstrip(".") not in hosts:
        raise ZhaopinDetailError(f"detail URL host is not allowlisted: {parsed.hostname}")

    initial = _extract_initial_data(html)
    main = initial.get("main")
    detail = main.get("positionDetail") if isinstance(main, dict) else None
    if not isinstance(detail, dict):
        raise ZhaopinDetailError("official detail is missing main.positionDetail")
    campus = main.get("campusJobDetail") if isinstance(main, dict) else {}
    if not isinstance(campus, dict):
        campus = {}
    company_detail = main.get("companyDetail") if isinstance(main, dict) else {}
    if not isinstance(company_detail, dict):
        company_detail = {}

    job_number = _text(detail.get("positionNumber") or initial.get("positionNumber"))
    if not job_number:
        raise ZhaopinDetailError("official detail is missing positionNumber")
    if expected_job_number and job_number != _text(expected_job_number):
        raise ZhaopinDetailError(
            f"official detail positionNumber mismatch: expected {expected_job_number}, got {job_number}"
        )

    title = _text(detail.get("positionName"))
    job_desc_html = str(detail.get("jobDesc") or detail.get("jobDescHighlight") or "")
    job_desc = clean_text(BeautifulSoup(job_desc_html, "html.parser").get_text(" ", strip=True))
    if not title or not job_desc:
        raise ZhaopinDetailError("official detail is missing title or jobDesc")

    degree = _text(detail.get("education"))
    location = _detail_location(detail, job_desc)
    if not degree:
        raise ZhaopinDetailError("official detail is missing education")
    if not location:
        raise ZhaopinDetailError("official detail is missing work location")

    deadline = _date(detail.get("dateEnd")) or _date(campus.get("applyEndTime")) or extract_deadline(job_desc)
    if not deadline:
        raise ZhaopinDetailError("official detail is missing a parseable deadline")
    degree_requirement = extract_zhaopin_degree_requirement(job_desc, degree)
    major_requirement = extract_zhaopin_major_requirement(job_desc)
    if not major_requirement:
        raise ZhaopinDetailError("official detail is missing a job-level major requirement")
    published = _date(detail.get("dateStart") or detail.get("positionPublishTime")) or extract_published_date(job_desc)

    quantity_value = detail.get("recruitNumber")
    if quantity_value in (None, "", 0, "0", "0.0"):
        quantity_value = detail.get("recruitPosition")
    quantity = _text(quantity_value)
    if not quantity or quantity in {"0", "0.0"}:
        quantity = "若干（官方未披露具体人数）"

    employer = _text(
        campus.get("companyName")
        or company_detail.get("campusCompanyName")
        or company_detail.get("displayOrgName")
        or company_detail.get("businessLicenseName")
    )
    if not employer:
        raise ZhaopinDetailError("official detail is missing employer")

    page_url = _text(detail.get("positionURL") or detail.get("positionUrl")) or detail_url
    page_host = urlparse(page_url).hostname
    if page_host and page_host.lower().rstrip(".") not in hosts:
        raise ZhaopinDetailError(f"official detail position URL host is not allowlisted: {page_host}")

    return {
        "title": title,
        "employer": employer,
        "job_number": job_number,
        "detail_url": detail_url,
        "position_url": page_url,
        "major_text": major_requirement,
        # Keep the portal's normalized enum for backwards-compatible display
        # fields; the evidence object carries the authoritative degree floor.
        "degree": degree,
        "location": location,
        "deadline": deadline,
        "published": published,
        "quantity": quantity,
        "description": job_desc,
        "evidence": {
            "evidence_scope": "official_zhaopin_detail_initial_data",
            "岗位": title,
            "招聘单位": employer,
            "专业要求": major_requirement,
            "专业范围": major_requirement,
            "学历要求": degree_requirement,
            "工作地点": location,
            "招聘人数": quantity,
            "报名截止": deadline,
            "岗位编号": job_number,
            "官方岗位详情": detail_url,
            "官方详情数据": "window.__INITIAL_DATA__.main.positionDetail",
        },
    }


__all__ = [
    "ZhaopinDetailError",
    "extract_zhaopin_degree_requirement",
    "extract_zhaopin_major_requirement",
    "parse_zhaopin_detail_html",
]
