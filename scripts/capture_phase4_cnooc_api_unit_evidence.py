"""Capture read-only evidence from the official CNOOC recruitment API.

This script is deliberately an evidence producer, not a job importer.  It
uses the same public API and host allow-list as the CNOOC browser source, but
stores the raw responses and a normalized audit report only.  A shared group
portal is never promoted to an independent unit recruitment entry.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_hub.browser_capture import _robots_permit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "current" / "phase4-cnooc-api-unit-evidence-2026-10-20.json"
DEFAULT_RETAIN_DIR = ROOT / "runtime" / "third-party-evidence" / "cnooc-api-2026-10-20"
PLATFORM_URL = "https://cnooc.zhaopin.com/job/index.html"
API_URL = "https://fe.zhaopin.com/grace/api/dsc/search-job-list"
API_HOSTS = {"fe.zhaopin.com"}
DETAIL_HOSTS = {"xiaoyuan.zhaopin.com", "cnooc.zhaopin.com"}
COMPANY_ID = "105147"
USER_AGENT = "job-hub-phase4-official-evidence/1.0"
INCLUDE_RE = re.compile(r"地质|勘查|物探|化探|测井|地球物理|油气|石油|资源|储层|海洋", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    value = html.unescape(str(value or ""))
    return " ".join(TAG_RE.sub(" ", value).split()).strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _allowlisted_url(value: Any, hosts: set[str], field: str) -> str:
    url = _text(value)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} must be an HTTP(S) URL")
    if parsed.hostname.lower().rstrip(".") not in hosts:
        raise ValueError(f"{field} host is not allowlisted: {parsed.hostname}")
    return url


def _payload(page_index: int, page_size: int) -> dict[str, Any]:
    return {
        "orgNumbers": [COMPANY_ID],
        "jobSource": 2,
        "pageIndex": page_index,
        "pageSize": page_size,
        "orgDepartmentIds": [],
        "workRegionIds": "",
        "jobTypes": "",
        "priorityMajors": "",
        "customTags": "",
    }


def _normalize_job(raw: dict[str, Any]) -> dict[str, Any]:
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    job = raw.get("job") if isinstance(raw.get("job"), dict) else {}
    detail_url = _allowlisted_url(job.get("url"), DETAIL_HOSTS, "detail_url")
    delivery_url = _allowlisted_url(job.get("deliveryPath"), {"campus1.zhaopin.com"}, "delivery_url")
    detail = _text(job.get("detail"))
    major = _text(job.get("needMajor")) or detail
    return {
        "external_id": _text(job.get("jobNumber")) or str(job.get("id") or ""),
        "title": _text(job.get("title")),
        "employer": _text(company.get("campusOrgName")),
        "location": _text(job.get("cityName")) or _text(job.get("address")),
        "address": _text(job.get("address")),
        "degree": _text(job.get("minEducationName")),
        "major": major,
        "detail_url": detail_url,
        "official_application_url": delivery_url,
        "deadline": _text(job.get("endDate")),
        "modified_time_epoch_ms": job.get("modifiedTime"),
        "geoscience_candidate": bool(
            INCLUDE_RE.search(" ".join((detail, major, _text(job.get("title")))))
        ),
    }


def _load_catalog_names() -> dict[str, set[str]]:
    path = ROOT / "data" / "three_oil_organization_catalog.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(group.get("id")): {
            str(unit.get("name"))
            for unit in group.get("units", [])
            if isinstance(unit, dict) and str(unit.get("name") or "").strip()
        }
        for group in payload.get("groups", [])
        if isinstance(group, dict)
    }


def capture(*, output: Path = DEFAULT_OUTPUT, retain_dir: Path = DEFAULT_RETAIN_DIR, page_size: int = 100) -> dict[str, Any]:
    """Fetch all public pages and write an auditable report without DB writes."""

    output = output.resolve()
    retain_dir = retain_dir.resolve()
    try:
        retain_dir.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("retain_dir must remain inside the project root") from exc

    _allowlisted_url(PLATFORM_URL, {"cnooc.zhaopin.com"}, "platform_url")
    _allowlisted_url(API_URL, API_HOSTS, "api_url")
    robots_cache: dict[str, Any] = {}
    _robots_permit(PLATFORM_URL, user_agent=USER_AGENT, robots_cache=robots_cache)
    _robots_permit(API_URL, user_agent=USER_AGENT, robots_cache=robots_cache)

    retain_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://cnooc.zhaopin.com",
        "Referer": PLATFORM_URL,
        "User-Agent": USER_AGENT,
    }
    page_records: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    try:
        page_index = 1
        total_pages = None
        while total_pages is None or page_index <= total_pages:
            request_payload = _payload(page_index, page_size)
            response = session.post(API_URL, json=request_payload, headers=headers, timeout=30)
            response.raise_for_status()
            raw_bytes = bytes(response.content)
            raw_path = retain_dir / f"page-{page_index:03d}.json"
            raw_path.write_bytes(raw_bytes)
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("code")) != "200":
                raise RuntimeError("official CNOOC API returned a non-success business response")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            page_info = data.get("pageInfo") if isinstance(data.get("pageInfo"), dict) else {}
            page_rows = data.get("jobList") if isinstance(data.get("jobList"), list) else []
            total_pages = int(page_info.get("totalPage") or 1)
            rows.extend(_normalize_job(item) for item in page_rows if isinstance(item, dict))
            page_records.append({
                "page_index": page_index,
                "request_payload": request_payload,
                "http_status": response.status_code,
                "response_bytes": len(raw_bytes),
                "response_sha256": _sha256_bytes(raw_bytes),
                "raw_response_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
                "row_count": len(page_rows),
                "total_num": int(page_info.get("totalNum") or 0),
                "total_pages": total_pages,
            })
            page_index += 1
    finally:
        session.close()

    catalog = _load_catalog_names()
    employers = sorted({row["employer"] for row in rows if row["employer"]})
    catalog_all = set().union(*catalog.values()) if catalog else set()
    report = {
        "phase": "phase4",
        "captured_at": _iso_now(),
        "evidence_type": "official_public_api_read_only",
        "source": {
            "source_id": "cnooc-career-browser",
            "platform_url": PLATFORM_URL,
            "api_url": API_URL,
            "company_id": COMPANY_ID,
            "user_agent": USER_AGENT,
            "robots_checked": True,
            "page_count": len(page_records),
            "pages": page_records,
        },
        "summary": {
            "official_job_card_count": len(rows),
            "geoscience_candidate_count": sum(1 for row in rows if row["geoscience_candidate"]),
            "employer_count": len(employers),
            "employers": employers,
            "employers_in_formal_catalog": sorted(set(employers) & catalog_all),
            "employers_not_in_formal_catalog": sorted(set(employers) - catalog_all),
            "catalog_units_observed": len(set(employers) & catalog_all),
        },
        "geoscience_jobs": [row for row in rows if row["geoscience_candidate"]],
        "catalog_reconciliation": {
            "formal_catalog_units_by_group": {key: len(value) for key, value in catalog.items()},
            "displayed_employer_names_are_not_legal_identity_proof": True,
            "shared_portal_is_not_independent_unit_entry": True,
            "catalog_changed": False,
        },
        "policy_checks": {
            "database_written": False,
            "candidate_leads_written": False,
            "third_party_url_used_as_application": False,
            "robots_or_captcha_bypass": False,
            "server_deployment": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retain-dir", type=Path, default=DEFAULT_RETAIN_DIR)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    report = capture(output=args.output, retain_dir=args.retain_dir, page_size=max(1, min(args.page_size, 100)))
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
