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


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return parse_date_value(text) or extract_deadline(text)


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
    location = _text(
        detail.get("workAddress")
        or detail.get("positionWorkCity")
        or detail.get("positionCityDistrict")
        or extract_location_hint(job_desc)
    )
    if not degree:
        raise ZhaopinDetailError("official detail is missing education")
    if not location:
        raise ZhaopinDetailError("official detail is missing work location")

    deadline = _date(detail.get("dateEnd")) or _date(campus.get("applyEndTime")) or extract_deadline(job_desc)
    if not deadline:
        raise ZhaopinDetailError("official detail is missing a parseable deadline")
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
        "major_text": job_desc,
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
            "专业要求": job_desc,
            "专业范围": job_desc,
            "学历要求": degree,
            "工作地点": location,
            "招聘人数": quantity,
            "报名截止": deadline,
            "岗位编号": job_number,
            "官方岗位详情": detail_url,
            "官方详情数据": "window.__INITIAL_DATA__.main.positionDetail",
        },
    }


__all__ = ["ZhaopinDetailError", "parse_zhaopin_detail_html"]
