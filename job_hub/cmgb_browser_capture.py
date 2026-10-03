"""Official browser capture contract for the CMGB/国聘 campus portal.

The 国聘 page is a JavaScript application: the list card is not the evidence
and a card can only be published after its official detail tab has been read.
This module keeps the browser concern separate from the normal HTTP collector.
It validates a capture manifest before any row reaches the publication gate.

The capture producer intentionally fails closed.  A blocked detail tab, a
missing page, or an incomplete pagination pass is recorded as ``partial`` or
``access_limited`` and never becomes an empty successful scan.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from job_hub.browser_capture import BrowserCaptureError, _robots_permit
from job_hub.capture_evidence import ensure_capture_manifest
from job_hub.capture_evidence import validate_capture_manifest
from job_hub.cnpc_browser_runner import _resolve_cdp_websocket
from job_hub.contracts import is_http_url


CMGB_CAPTURE_STATUSES = frozenset({"success", "partial", "access_limited", "parse_failed"})
CMGB_DEFAULT_HOSTS = {"cmgb.iguopin.com", "www.iguopin.com", "iguopin.com", "www.cmgb.com.cn"}
CMGB_REQUIRED_ROW_FIELDS = (
    "external_id",
    "title",
    "employer",
    "major",
    "degree",
    "location",
    "headcount",
    "deadline",
    "detail_url",
    "evidence_url",
)
CMGB_REQUIRED_EVIDENCE_KEYS = (
    "岗位",
    "专业范围",
    "学历要求",
    "工作地点",
    "招聘人数",
    "报名截止",
    "官方详情链接",
)


class CmgbBrowserCaptureError(ValueError):
    """Raised when a CMGB capture is unsafe to publish."""


def _text(value: Any, field: str) -> str:
    result = " ".join(str(value or "").split()).strip()
    if not result:
        raise CmgbBrowserCaptureError(f"{field} must be non-empty")
    return result


def _int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise CmgbBrowserCaptureError(f"{field} must be an integer") from error
    if result < 0:
        raise CmgbBrowserCaptureError(f"{field} cannot be negative")
    return result


def _timestamp(value: Any, field: str = "captured_at") -> str:
    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise CmgbBrowserCaptureError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise CmgbBrowserCaptureError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _official_url(value: Any, field: str, hosts: set[str]) -> str:
    result = _text(value, field)
    if not is_http_url(result):
        raise CmgbBrowserCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(result).hostname or "").lower().rstrip(".")
    if hosts and host not in hosts:
        raise CmgbBrowserCaptureError(f"{field} host is not allowlisted: {host}")
    return result


def _scan_metrics(payload: dict[str, Any], *, require_complete: bool) -> dict[str, Any]:
    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise CmgbBrowserCaptureError("scan metrics are required")
    required = (
        "pages_scanned",
        "pagination_complete",
        "rows_discovered",
        "rows_exported",
        "failed_rows",
        "detail_discovered",
        "detail_succeeded",
        "detail_failed",
    )
    normalized = {field: _int(scan.get(field), f"scan.{field}") for field in required if field != "pagination_complete"}
    if "pagination_complete" not in scan:
        raise CmgbBrowserCaptureError("scan.pagination_complete is required")
    normalized["pagination_complete"] = bool(scan["pagination_complete"])
    if normalized["rows_exported"] > normalized["rows_discovered"]:
        raise CmgbBrowserCaptureError("scan.rows_exported exceeds scan.rows_discovered")
    if normalized["detail_succeeded"] + normalized["detail_failed"] > normalized["detail_discovered"]:
        raise CmgbBrowserCaptureError("detail outcomes exceed detail_discovered")
    complete = (
        normalized["pagination_complete"]
        and normalized["failed_rows"] == 0
        and normalized["detail_failed"] == 0
        and normalized["detail_succeeded"] == normalized["detail_discovered"]
        and normalized["rows_exported"] == normalized["rows_discovered"]
    )
    if require_complete and not complete:
        raise CmgbBrowserCaptureError("CMGB capture is incomplete; publication is blocked")
    return {**scan, **normalized}


def _read_cmgb_capture_payload(path: Path | str) -> dict[str, Any]:
    capture_path = Path(path)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CmgbBrowserCaptureError(f"cannot read CMGB capture: {capture_path}") from error
    if not isinstance(payload, dict):
        raise CmgbBrowserCaptureError("CMGB capture must be a JSON object")
    return payload


def _cmgb_allowed_hosts(allowed_hosts: Iterable[str] | None) -> set[str]:
    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in (allowed_hosts or CMGB_DEFAULT_HOSTS)
        if str(host).strip()
    }
    if not hosts:
        raise CmgbBrowserCaptureError("allowed_hosts must not be empty")
    return hosts


def _capture_age_hours(
    captured_at: str,
    *,
    now: datetime | None,
    max_age_hours: float | None,
) -> float:
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = (current - captured).total_seconds() / 3600
    if age_hours < -1:
        raise CmgbBrowserCaptureError("captured_at is in the future")
    if max_age_hours is not None and age_hours > float(max_age_hours):
        raise CmgbBrowserCaptureError(
            f"CMGB capture is stale ({age_hours:.1f}h > {float(max_age_hours):.1f}h)"
        )
    return max(0.0, age_hours)


def _normalize_cmgb_rows(rows: Any, *, hosts: set[str]) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise CmgbBrowserCaptureError("CMGB capture rows must be a non-empty list")
    normalized_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise CmgbBrowserCaptureError(f"row {index} must be an object")
        row = dict(raw)
        for field in CMGB_REQUIRED_ROW_FIELDS:
            row[field] = _text(row.get(field), f"row {index}.{field}")
        if row["external_id"] in seen:
            raise CmgbBrowserCaptureError(f"duplicate CMGB external_id: {row['external_id']}")
        seen.add(row["external_id"])
        row["detail_url"] = _official_url(row["detail_url"], f"row {index}.detail_url", hosts)
        row["evidence_url"] = _official_url(row["evidence_url"], f"row {index}.evidence_url", hosts)
        evidence = row.get("field_evidence")
        if not isinstance(evidence, dict):
            raise CmgbBrowserCaptureError(f"row {index}.field_evidence is required")
        for key in CMGB_REQUIRED_EVIDENCE_KEYS:
            if not str(evidence.get(key) or "").strip():
                raise CmgbBrowserCaptureError(f"row {index}.field_evidence.{key} is required")
        row["field_evidence"] = {
            str(key): _text(value, f"row {index}.field_evidence.{key}")
            for key, value in evidence.items()
        }
        # Captures made before a parser improvement still retain the rendered
        # job-duty text. Reconcile an ambiguous overview value with an exact
        # major sentence from that same official detail before it enters the
        # publication gate. This is not inference from the title or employer:
        # both values originate in the job's own official detail page.
        description = " ".join(str(row.get("description") or "").split()).strip()
        detail_major = _major_from_description(description)
        if detail_major and _major_needs_detail_evidence(row["major"]):
            row["field_evidence"].setdefault("概览专业范围", row["major"])
            row["field_evidence"]["专业范围"] = detail_major
            row["field_evidence"]["专业证据定位"] = "官方详情职位介绍"
            row["major"] = detail_major
        normalized_rows.append(row)
    return normalized_rows


def load_cmgb_browser_capture(
    path: Path | str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    max_age_hours: float | None = 30,
    require_complete_scan: bool = True,
    require_capture_manifest: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load and validate one server-generated CMGB browser manifest."""

    payload = _read_cmgb_capture_payload(path)
    if require_capture_manifest:
        try:
            validate_capture_manifest(payload)
        except ValueError as error:
            raise CmgbBrowserCaptureError(str(error)) from error
    status = _text(payload.get("status"), "status")
    if status not in CMGB_CAPTURE_STATUSES:
        raise CmgbBrowserCaptureError(f"unsupported CMGB capture status: {status}")
    if status != "success":
        raise CmgbBrowserCaptureError(f"CMGB capture is not publishable: {status}")
    hosts = _cmgb_allowed_hosts(allowed_hosts)
    platform_url = _official_url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _timestamp(payload.get("captured_at"))
    age_hours = _capture_age_hours(
        captured_at,
        now=now,
        max_age_hours=max_age_hours,
    )
    scan = _scan_metrics(payload, require_complete=require_complete_scan)
    normalized_rows = _normalize_cmgb_rows(payload.get("rows"), hosts=hosts)
    if scan["rows_exported"] != len(normalized_rows):
        raise CmgbBrowserCaptureError("scan.rows_exported does not match rows length")
    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "capture_age_hours": round(max(0.0, age_hours), 3),
        "scan": scan,
        "rows": normalized_rows,
    }


def _cmgb_external_id(detail_url: str, fallback: str) -> str:
    detail_id = parse_qs(urlparse(detail_url).query).get("id", [""])[0].strip()
    return f"cmgb-iguopin-{detail_id}" if detail_id else _text(fallback, "card_id")


def _cmgb_official_detail_url(value: Any, field: str, hosts: set[str]) -> str:
    detail_url = _official_url(value, field, hosts)
    parsed = urlparse(detail_url)
    detail_id = parse_qs(parsed.query).get("id", [""])[0].strip()
    if "/job/detail" not in parsed.path or not detail_id:
        raise CmgbBrowserCaptureError(f"{field} must be a concrete CMGB job detail URL")
    return detail_url


def load_cmgb_detail_retry_capture(
    path: Path | str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    max_age_hours: float | None = 12,
    require_capture_manifest: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load a recent, fully paginated partial capture for detail-only retry.

    This does not make a partial capture publishable. It verifies that its
    completed rows and failed official detail URLs describe one frozen list
    pass, so a later retry can complete that exact pass without returning to
    the discovery pages.
    """

    payload = _read_cmgb_capture_payload(path)
    if require_capture_manifest:
        try:
            validate_capture_manifest(payload)
        except ValueError as error:
            raise CmgbBrowserCaptureError(str(error)) from error
    status = _text(payload.get("status"), "status")
    if status != "partial":
        raise CmgbBrowserCaptureError("CMGB detail retry requires a partial capture")
    hosts = _cmgb_allowed_hosts(allowed_hosts)
    platform_url = _official_url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _timestamp(payload.get("captured_at"))
    age_hours = _capture_age_hours(
        captured_at,
        now=now,
        max_age_hours=max_age_hours,
    )
    scan = _scan_metrics(payload, require_complete=False)
    if not scan["pagination_complete"]:
        raise CmgbBrowserCaptureError(
            "CMGB detail retry requires a completed pagination pass"
        )
    if scan["detail_failed"] <= 0 or scan["failed_rows"] <= 0:
        raise CmgbBrowserCaptureError("CMGB partial capture has no failed detail rows to retry")
    if scan["detail_discovered"] != scan["rows_discovered"]:
        raise CmgbBrowserCaptureError("CMGB retry capture detail and row totals disagree")
    if scan["failed_rows"] != scan["detail_failed"]:
        raise CmgbBrowserCaptureError("CMGB retry capture failed row and detail totals disagree")
    normalized_rows = _normalize_cmgb_rows(payload.get("rows"), hosts=hosts)
    if scan["rows_exported"] != len(normalized_rows):
        raise CmgbBrowserCaptureError("CMGB retry rows_exported does not match rows length")
    if scan["detail_succeeded"] != len(normalized_rows):
        raise CmgbBrowserCaptureError("CMGB retry detail_succeeded does not match rows length")
    if len(normalized_rows) + scan["detail_failed"] != scan["detail_discovered"]:
        raise CmgbBrowserCaptureError("CMGB retry rows cannot account for all discovered details")

    raw_failures = scan.get("failure_records")
    if not isinstance(raw_failures, list) or len(raw_failures) != scan["detail_failed"]:
        raise CmgbBrowserCaptureError("CMGB retry failure_records do not match failed details")
    existing_ids = {str(row["external_id"]) for row in normalized_rows}
    targets: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for index, raw in enumerate(raw_failures, start=1):
        if not isinstance(raw, dict):
            raise CmgbBrowserCaptureError(f"retry failure record {index} must be an object")
        card_id = _text(raw.get("card_id"), f"retry failure record {index}.card_id")
        detail_url = _cmgb_official_detail_url(
            raw.get("detail_url"),
            f"retry failure record {index}.detail_url",
            hosts,
        )
        external_id = _cmgb_external_id(detail_url, card_id)
        if external_id in existing_ids or external_id in seen_targets:
            raise CmgbBrowserCaptureError(f"duplicate CMGB retry target: {external_id}")
        seen_targets.add(external_id)
        targets.append(
            {
                "external_id": external_id,
                "page": _int(raw.get("page"), f"retry failure record {index}.page"),
                "row": _int(raw.get("row"), f"retry failure record {index}.row"),
                "card_id": card_id,
                "title": " ".join(str(raw.get("title") or "").split()).strip(),
                "employer": " ".join(str(raw.get("employer") or "").split()).strip(),
                "detail_url": detail_url,
                "reason": " ".join(str(raw.get("reason") or "").split()).strip(),
            }
        )
    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "capture_age_hours": round(age_hours, 3),
        "scan": scan,
        "rows": normalized_rows,
        "retry_targets": targets,
    }


def build_cmgb_detail_retry_payload(
    retry_capture: dict[str, Any],
    *,
    retried_rows: list[dict[str, Any]],
    retry_failures: list[dict[str, Any]],
    captured_at: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Merge a target retry into its frozen partial capture.

    A success is possible only if every failed target returns one complete,
    unique official-detail row. Any remaining target stays in a new partial
    payload, which ``persist_cmgb_browser_capture`` archives instead of
    replacing the canonical success path.
    """

    hosts = _cmgb_allowed_hosts(allowed_hosts)
    base_rows = _normalize_cmgb_rows(retry_capture.get("rows"), hosts=hosts)
    targets = retry_capture.get("retry_targets")
    if not isinstance(targets, list) or not targets:
        raise CmgbBrowserCaptureError("CMGB retry capture has no retry targets")
    target_by_id = {
        _text(target.get("external_id"), "retry target external_id"): dict(target)
        for target in targets
        if isinstance(target, dict)
    }
    if len(target_by_id) != len(targets):
        raise CmgbBrowserCaptureError("CMGB retry targets must have unique identifiers")

    normalized_retry_rows = (
        _normalize_cmgb_rows(retried_rows, hosts=hosts) if retried_rows else []
    )
    completed_by_id = {
        str(row["external_id"]): row for row in normalized_retry_rows
    }
    if set(completed_by_id) - set(target_by_id):
        raise CmgbBrowserCaptureError("CMGB retry returned a row outside the failed target set")
    if len(completed_by_id) != len(normalized_retry_rows):
        raise CmgbBrowserCaptureError("CMGB retry returned duplicate detail rows")

    failures_by_id: dict[str, dict[str, Any]] = {}
    for raw in retry_failures:
        if not isinstance(raw, dict):
            raise CmgbBrowserCaptureError("CMGB retry failure record must be an object")
        external_id = _text(raw.get("external_id"), "retry failure external_id")
        if external_id not in target_by_id:
            raise CmgbBrowserCaptureError("CMGB retry failure is outside the failed target set")
        if external_id in completed_by_id or external_id in failures_by_id:
            raise CmgbBrowserCaptureError("CMGB retry target has conflicting outcomes")
        target = dict(target_by_id[external_id])
        target["reason"] = " ".join(str(raw.get("reason") or "").split()).strip() or (
            "official detail retry did not return a complete record"
        )
        failures_by_id[external_id] = target
    missing = set(target_by_id) - set(completed_by_id) - set(failures_by_id)
    if missing:
        raise CmgbBrowserCaptureError("CMGB retry did not record every target outcome")

    combined_rows = _normalize_cmgb_rows(
        [*base_rows, *normalized_retry_rows],
        hosts=hosts,
    )
    scan = dict(retry_capture.get("scan") or {})
    total = _int(scan.get("detail_discovered"), "scan.detail_discovered")
    remaining_failures = [failures_by_id[key] for key in target_by_id if key in failures_by_id]
    if len(combined_rows) + len(remaining_failures) != total:
        raise CmgbBrowserCaptureError("CMGB retry outcomes do not account for the frozen detail total")
    completed = not remaining_failures
    merged_scan = {
        **scan,
        "pagination_complete": True,
        "rows_exported": len(combined_rows),
        "failed_rows": len(remaining_failures),
        "detail_succeeded": len(combined_rows),
        "detail_failed": len(remaining_failures),
        "failure_records": remaining_failures,
    }
    merged_scan.pop("failure_reason", None)
    result = {
        "version": 1,
        "status": "success" if completed else "partial",
        "platform_url": retry_capture["platform_url"],
        "captured_at": _timestamp(
            captured_at
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ),
        "retry_of": str(retry_capture.get("archive_file") or "partial-capture"),
        "scan": merged_scan,
        "rows": combined_rows,
    }
    # Preserve the invariant in the same place used for canonical writes.
    _scan_metrics(result, require_complete=completed)
    return result


def _cmgb_row_from_detail(
    detail: dict[str, Any],
    *,
    target: dict[str, Any],
) -> dict[str, Any]:
    title = " ".join(str(detail.get("title") or target.get("title") or "").split()).strip()
    employer = " ".join(
        str(detail.get("employer") or target.get("employer") or "").split()
    ).strip()
    if not title or not employer:
        raise BrowserCaptureError(
            "CMGB detail retry is missing title or employer in both official detail and list card"
        )
    external_id = _cmgb_external_id(str(detail["detail_url"]), str(target["card_id"]))
    if external_id != str(target["external_id"]):
        raise BrowserCaptureError("CMGB detail retry resolved a different official job identifier")
    return {
        "external_id": external_id,
        "title": title,
        "employer": employer,
        "major": detail["major"],
        "degree": detail["degree"],
        "location": detail["location"],
        "headcount": detail["headcount"],
        "deadline": detail["deadline"],
        "detail_url": detail["detail_url"],
        "evidence_url": detail["evidence_url"],
        "description": detail["description"],
        "field_evidence": detail["field_evidence"],
    }


def _detail_retryable(error: BaseException) -> bool:
    """Classify transient detail failures without retrying access denials.

    A second browser attempt is useful for SPA render races and renderer
    timeouts.  It must not turn robots, authentication, rate limiting, or an
    explicit server policy response into repeated probing.
    """

    message = str(error).lower()
    non_retryable = (
        "robots" in message,
        "http 401" in message,
        "http 403" in message,
        "http 412" in message,
        "http 429" in message,
        "access denied" in message,
    )
    return not any(non_retryable)


def _detail_retry_needs_session_reset(error: BaseException) -> bool:
    """Return whether a failed detail attempt likely poisoned its browser target.

    A normal field timeout can be retried in the same context.  Chromium/CDP
    renderer failures are different: keeping the context alive tends to make
    every subsequent page inherit the broken target.  Reconnecting is bounded
    to these explicit browser-runtime signals and never changes the evidence
    or access-policy gates.
    """

    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "target crashed",
            "target closed",
            "browser has been closed",
            "browser disconnected",
            "execution context was destroyed",
            "connect_over_cdp",
        )
    )


def _body_text(page: Any) -> str:
    return " ".join(str(page.locator("body").inner_text() or "").split()).strip()


def _first_text(container: Any, selectors: Iterable[str]) -> str:
    for selector in selectors:
        locator = container.locator(selector).first
        if locator.count():
            value = " ".join(str(locator.inner_text() or "").split()).strip()
            if value:
                return value
    return ""


def _overview_value(page: Any, labels: Iterable[str]) -> str:
    """Read a value from 国聘's structured overview block.

    The overview is more reliable than matching the whole body because the
    live page deliberately renders ``专业要求：详见职位描述`` while the
    actual professional evidence is in the job-duty section below it.
    """

    wanted = {str(label).strip().rstrip("：:") for label in labels}
    items = page.locator(".overview-item")
    for index in range(items.count()):
        item = items.nth(index)
        label = _first_text(item, (".overview-title",)).rstrip("：:")
        if label not in wanted:
            continue
        value = _first_text(item, (".overview-desc",))
        if value:
            return value
    return ""


def _is_major_placeholder(value: str) -> bool:
    return " ".join(str(value or "").split()).strip() in {
        "详见职位描述",
        "见职位描述",
        "请见职位描述",
    }


def _is_generic_major_summary(value: str) -> bool:
    """Return whether an overview value needs its detail prose to disambiguate it.

    A category such as ``地质类`` is useful discovery metadata, but does not
    prove that one of the four supported CUPB Geoscience programmes is
    eligible.  The same official detail frequently provides a precise
    ``专业背景`` or ``专业需求`` sentence below the overview.  We only replace
    the generic summary when that sentence is present; otherwise the normal
    student-publication gate still rejects the ambiguous category.
    """

    normalized = " ".join(str(value or "").split()).strip(" 。；;：:")
    generic_labels = {
        "地质类",
        "资源勘查类",
        "地球物理学类",
        "地球化学类",
        "水文地质类",
        "矿业类",
        "地球科学类",
    }
    values = {
        item.strip()
        for item in re.split(r"[、，,；;]", normalized)
        if item.strip() and not _is_major_placeholder(item)
    }
    return bool(values) and values.issubset(generic_labels)


def _major_needs_detail_evidence(value: str) -> bool:
    """Whether a captured overview should be reconciled with its prose."""

    normalized = " ".join(str(value or "").split()).strip()
    return (
        not normalized
        or _is_major_placeholder(normalized)
        or "详见职位描述" in normalized
        or _is_generic_major_summary(normalized)
    )


def _major_from_description(text: str) -> str:
    """Extract the explicit major line from 国聘's job-duty prose."""

    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    labels = (
        "专业要求",
        "专业范围",
        "需求专业",
        "专业背景",
        "专业需求",
        "专业类别",
        "所学专业",
    )
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[：:]\s*(.+?)(?=\s*(?:"
            r"[二三四五六七八九十]+、|\d+[、.．)]|职责描述|岗位职责|任职要求|"
            r"工作内容|专业技能|现场执行能力|实践与规范意识|个人素质|适应能力|"
            r"软件应用|$)|[。；;])",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            value = " ".join(match.group(1).split()).strip(" ：:;；")
            if value:
                return value

    # A small set of official detail pages use an unlabeled first line under
    # "任职要求", for example "1.地质学；...；煤炭地质勘查相关专业。".
    # It is accepted only when that same requirement sentence explicitly ends
    # in a professional requirement, never from a responsibility paragraph.
    requirement_line = re.search(
        r"(?:任职要求|任职条件)\s*(?:[：:]\s*)?(?:1[、.．)]\s*)"
        r"([^。；;]{1,180}?专业[^。；;]{0,80})(?:[。；;]|$)",
        normalized,
        flags=re.IGNORECASE,
    )
    if requirement_line:
        return " ".join(requirement_line.group(1).split()).strip(" ：:;；")

    # Some official details put the major only in an employment-condition
    # sentence, for example ``具备地球化学等化探类相关专业``.  This remains
    # job-level evidence, while the student publication gate decides whether
    # the wording is an exact target-major match or needs review.
    condition = re.search(
        r"(?:具备|要求|(?:【)?任职条件(?:】)?|专业背景|所学专业)\s*[：:]?\s*"
        r"(?:\d+[.、)]\s*)*([^。；;\n]{1,120}?专业)(?:[^。；;\n]{0,24})?(?:[。；;]|$)",
        normalized,
        flags=re.IGNORECASE,
    )
    if condition:
        return " ".join(condition.group(1).split()).strip(" ：:;；")

    # Some public detail templates use a plain qualification sentence rather
    # than a labelled field, e.g. ``地质类相关专业，本科及以上学历``.  Accept
    # only an explicit target-discipline phrase ending in ``专业`` and only
    # when the same sentence also contains a qualification marker.  This must
    # not turn a responsibility or employer introduction into major evidence.
    explicit = re.search(
        r"([^。；;\n]{0,80}?(?:地质|地球物理|资源勘查|油气勘探|"
        r"石油地质|地球化学|水文地质|工程地质|物探|测井|遥感地质)"
        r"[^。；;\n]{0,80}?(?:相关)?专业[^。；;\n]{0,80})",
        normalized,
        flags=re.IGNORECASE,
    )
    if explicit and re.search(
        r"(?:本科|硕士|博士|学历|学位|任职|招聘|应聘|要求)", explicit.group(1)
    ):
        return " ".join(explicit.group(1).split()).strip(" ：:;；")
    return ""


def _label_value(text: str, labels: Iterable[str]) -> str:
    all_labels = (
        "专业要求",
        "专业范围",
        "需求专业",
        "专业",
        "最低学历",
        "学历要求",
        "学历",
        "工作地点",
        "工作城市",
        "工作地区",
        "地点",
        "招聘人数",
        "需求人数",
        "人数",
        "报名截止",
        "截止日期",
        "截止时间",
        "岗位名称",
        "职位名称",
        "招聘单位",
        "用人单位",
        "单位",
    )
    boundary = "|".join(re.escape(label) for label in all_labels)
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[：:]\s*(.*?)(?=\s+(?:{boundary})\s*[：:]|[|；;。\n]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if match and match.group(1).strip():
            return " ".join(match.group(1).split()).strip()
    return ""


def extract_cmgb_detail(
    page: Any,
    *,
    detail_url: str,
    allowed_hosts: set[str],
    require_employer: bool = True,
) -> dict[str, Any]:
    """Extract fields from a rendered official detail tab.

    Reading ``body.inner_text`` deliberately supports both text and generic
    nodes used by different versions of the React detail page.  Missing fields
    raise instead of producing a misleading partial job.
    """

    official_detail_url = _official_url(detail_url, "detail_url", allowed_hosts)
    body = _body_text(page)
    duty = _first_text(page, (".job-duty", ".job-introduction", ".job-description"))
    title = _first_text(page, (".job-banner .title", ".title", ".job-name", "h1", "h2"))
    employer = _first_text(
        page,
        (".company-title", ".company-name", ".requirement .company-name", ".company"),
    )
    major = _overview_value(page, ("专业要求", "专业范围", "需求专业"))
    detail_major = _major_from_description(duty)
    if _major_needs_detail_evidence(major):
        major = detail_major or _label_value(
            duty or body,
            (
                "专业要求",
                "专业范围",
                "需求专业",
                "专业背景",
                "专业需求",
                "专业类别",
                "所学专业",
                "专业",
            ),
        ) or major
    degree = _overview_value(page, ("最低学历", "学历要求", "学历")) or _label_value(
        body, ("最低学历", "学历要求", "学历")
    )
    location = _first_text(page, (".job-banner .address", ".address")) or _overview_value(
        page, ("工作地点", "工作城市", "工作地区", "地点")
    ) or _label_value(body, ("工作地点", "工作城市", "工作地区", "地点"))
    headcount = _overview_value(page, ("招聘人数", "需求人数", "人数")) or _label_value(
        body, ("招聘人数", "需求人数", "人数")
    )
    deadline = _overview_value(page, ("报名截止", "截止日期", "截止时间")) or _label_value(
        body, ("报名截止", "截止日期", "截止时间")
    )
    values = {
        "title": title or _label_value(body, ("岗位名称", "职位名称")),
        "employer": employer or _label_value(body, ("招聘单位", "用人单位", "单位")),
        "major": major,
        "degree": degree,
        "location": location,
        "headcount": headcount,
        "deadline": deadline,
    }
    missing = [
        field
        for field, value in values.items()
        if not value
        and field != "major"
        and (field != "employer" or require_employer)
    ]
    if not values["major"] or _is_major_placeholder(values["major"]):
        missing.append("major evidence")
    if missing:
        raise BrowserCaptureError(
            "CMGB detail is missing required fields: " + ", ".join(missing)
        )
    evidence = {
        "岗位": values["title"],
        "专业范围": values["major"],
        "学历要求": values["degree"],
        "工作地点": values["location"],
        "招聘人数": values["headcount"],
        "报名截止": values["deadline"],
        "官方详情链接": official_detail_url,
    }
    return {
        **values,
        "detail_url": official_detail_url,
        "evidence_url": official_detail_url,
        "field_evidence": evidence,
        "description": duty or body,
    }


def _write_capture(path: Path | str, payload: dict[str, Any]) -> dict[str, Any]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload


def _failure_archive_path(
    destination: Path,
    *,
    captured_at: str,
    status: str,
) -> Path:
    """Return a new, timestamped diagnostic path next to a canonical capture."""

    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    timestamp = captured.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_directory = destination.parent / f"{destination.stem}.failures"
    archive_directory.mkdir(parents=True, exist_ok=True)
    candidate = archive_directory / f"{destination.stem}-{timestamp}-{status}.json"
    sequence = 2
    while candidate.exists():
        candidate = archive_directory / (
            f"{destination.stem}-{timestamp}-{status}-{sequence}.json"
        )
        sequence += 1
    return candidate


def write_cmgb_capture_failure(
    *,
    output: Path | str,
    platform_url: str,
    status: str,
    reason: str,
    scan: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    captured_at: str | None = None,
    source_id: str = "cmgb-iguopin-browser",
    adapter_version: str = "cmgb-browser-v1",
) -> dict[str, Any]:
    """Archive a non-publishable diagnostic without replacing a good capture.

    ``output`` is the canonical, publishable manifest path. A browser timeout
    is an observation about this run, not evidence that every previously
    captured official position disappeared. Keep every diagnostic in a
    timestamped ``*.failures/`` archive. ``*.failure.json`` remains a copy of
    the newest diagnostic for operator convenience, but never replaces the
    canonical manifest. A transient portal failure therefore cannot erase the
    last complete capture or trigger a false withdrawal on the next sync.
    """

    if status not in {"access_limited", "parse_failed", "partial"}:
        raise ValueError("CMGB failure status must be access_limited, parse_failed or partial")
    destination = Path(output)
    timestamp = _timestamp(
        captured_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    if scan is not None and not isinstance(scan, dict):
        raise ValueError("CMGB failure scan must be an object")
    if rows is not None and not isinstance(rows, list):
        raise ValueError("CMGB failure rows must be a list")
    diagnostics = {
        "pages_scanned": 0,
        "pagination_complete": False,
        "rows_discovered": 0,
        "rows_exported": 0,
        "failed_rows": 0,
        "detail_discovered": 0,
        "detail_succeeded": 0,
        "detail_failed": 0,
        **(scan or {}),
        "failure_reason": str(reason)[:1000],
    }
    archive_path = _failure_archive_path(
        destination,
        captured_at=timestamp,
        status=status,
    )
    payload = {
        "version": 1,
        "status": status,
        "platform_url": platform_url,
        "diagnostic_for": destination.name,
        "archive_file": archive_path.relative_to(destination.parent).as_posix(),
        "captured_at": timestamp,
        "scan": diagnostics,
        "rows": rows or [],
    }
    payload = ensure_capture_manifest(
        payload,
        source_id=source_id,
        adapter_version=adapter_version,
    )
    _write_capture(archive_path, payload)
    _write_capture(destination.with_suffix(".failure.json"), payload)
    return payload


def persist_cmgb_browser_capture(
    *,
    output: Path | str,
    payload: dict[str, Any],
    source_id: str = "cmgb-iguopin-browser",
    adapter_version: str = "cmgb-browser-v1",
) -> dict[str, Any]:
    """Persist a CMGB run while protecting the last complete evidence snapshot.

    The configured capture path is consumed by the publication source and is
    therefore a success-only pointer. A partial crawl can still contain useful
    diagnosis and a subset of official details, but it is not a safe
    replacement for the previous complete inventory. It is archived instead.
    """

    payload = ensure_capture_manifest(
        payload,
        source_id=source_id,
        adapter_version=adapter_version,
    )
    status = _text(payload.get("status"), "status")
    if status not in CMGB_CAPTURE_STATUSES:
        raise CmgbBrowserCaptureError(f"unsupported CMGB capture status: {status}")
    if status == "success":
        # A producer cannot label an incomplete pass as a canonical success.
        scan = _scan_metrics(payload, require_complete=True)
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise CmgbBrowserCaptureError("successful CMGB capture rows must be a non-empty list")
        if scan["rows_exported"] != len(rows):
            raise CmgbBrowserCaptureError(
                "successful CMGB capture rows_exported does not match rows length"
            )
        return _write_capture(output, payload)

    scan = payload.get("scan")
    rows = payload.get("rows")
    if not isinstance(scan, dict):
        raise CmgbBrowserCaptureError("non-success CMGB capture is missing scan metrics")
    if not isinstance(rows, list):
        raise CmgbBrowserCaptureError("non-success CMGB capture rows must be a list")
    failure_records = scan.get("failure_records")
    reason = str(scan.get("failure_reason") or "").strip()
    if not reason and isinstance(failure_records, list) and failure_records:
        reason = str(failure_records[0].get("reason") or "").strip()
    if not reason:
        reason = f"CMGB browser capture ended with status {status}"
    return write_cmgb_capture_failure(
        output=output,
        platform_url=str(payload.get("platform_url") or ""),
        status=status,
        reason=reason,
        scan=scan,
        rows=rows,
        captured_at=str(payload.get("captured_at") or ""),
        source_id=source_id,
        adapter_version=adapter_version,
    )


def quarantine_cmgb_partial_capture(output: Path | str) -> dict[str, Any]:
    """Recover a legacy partial file that occupied the success-only path.

    Phase 84 introduced the success-only path after some servers had already
    written a ``partial`` manifest at that location. This explicit migration
    archives the diagnostic first, then moves the legacy input beside it. It
    never deletes the input and refuses to touch a successful capture.
    """

    destination = Path(output)
    payload = _read_cmgb_capture_payload(destination)
    status = _text(payload.get("status"), "status")
    if status != "partial":
        raise CmgbBrowserCaptureError(
            "only a legacy partial CMGB capture may be quarantined"
        )
    archived = persist_cmgb_browser_capture(output=destination, payload=payload)
    quarantine_path = destination.with_suffix(".legacy-partial.json")
    sequence = 2
    while quarantine_path.exists():
        quarantine_path = destination.with_suffix(f".legacy-partial-{sequence}.json")
        sequence += 1
    destination.replace(quarantine_path)
    return {
        "status": "quarantined",
        "canonical_path": str(destination),
        "quarantine_path": str(quarantine_path),
        "archive_file": archived["archive_file"],
        "captured_at": archived["captured_at"],
    }


def _close_context_pages(context: Any) -> None:
    """Close every page in the dedicated CMGB browser context.

    ``chromedp/headless-shell`` exposes one default CDP context and does not
    support creating incognito contexts.  The CMGB worker has its own browser
    container, so closing every page in this one context is safe and gives an
    interrupted capture a deterministic resource-recovery path.
    """

    try:
        pages = list(context.pages)
    except Exception:
        return
    for opened_page in pages:
        try:
            if not opened_page.is_closed():
                opened_page.close()
        except Exception:
            # Cleanup must never convert an already-complete capture into a
            # failure. The next browser restart remains an operator fallback.
            pass


def _close_browser_connection(browser: Any) -> None:
    """Close a Playwright connection without stopping a remote CDP browser.

    Playwright's ``Browser.close`` has different ownership semantics for a
    launched browser and a browser obtained with ``connect_over_cdp``: the
    former is stopped, while the latter is disconnected after its contexts
    are cleared. Calling it in both modes prevents long-running workers from
    accumulating Playwright websocket sessions.
    """

    if browser is None:
        return
    try:
        browser.close()
    except Exception:
        # A crashed renderer may already have disposed the connection. Cleanup
        # is best effort and must not replace the capture result or failure
        # evidence.
        pass


def run_cmgb_browser_capture(
    *,
    url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: Iterable[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Capture all visible CMGB pages and their official detail tabs.

    This is a conservative producer: it does not call hidden write APIs,
    submit forms, or bypass robots.  The selectors are configuration-driven so
    a portal DOM change can be reviewed without changing publication code.
    """

    hosts = {str(host).strip().lower().rstrip(".") for host in allowed_hosts if str(host).strip()}
    target_url = _official_url(url, "browser_url", hosts)
    _robots_permit(target_url, user_agent=user_agent)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before enabling CMGB capture"
        ) from error

    max_pages = max(1, int(config.get("max_pages", 20)))
    row_selector = str(config.get("row_selector") or ".ant-card")
    next_selector = str(config.get("next_selector") or ".ant-pagination-next")
    detail_button_selector = str(config.get("detail_button_selector") or "button")
    detail_popup_timeout_ms = max(
        1_000, min(timeout_ms, int(config.get("detail_popup_timeout_ms", 5_000)))
    )
    detail_render_wait_ms = max(250, min(10_000, int(config.get("detail_render_wait_ms", 1_000))))
    initial_render_wait_ms = max(
        detail_render_wait_ms,
        min(20_000, int(config.get("initial_render_wait_ms", 10_000))),
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_scanned = 0
    failed_rows = 0
    detail_failed = 0
    detail_discovered = 0
    failure_records: list[dict[str, Any]] = []
    pagination_complete = False
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(
                    _resolve_cdp_websocket(cdp_url)
                )
                if not browser.contexts:
                    raise BrowserCaptureError("CMGB CDP browser has no default context")
                # The dedicated chromedp image has a single default context
                # and rejects Target.createBrowserContext. Clear stale tabs
                # left by an interrupted earlier scan before this run creates
                # its list page. No other worker shares this CDP browser.
                context = browser.contexts[0]
                _close_context_pages(context)
            else:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(user_agent=user_agent)
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(initial_render_wait_ms)
            while pages_scanned < max_pages:
                pages_scanned += 1
                page.wait_for_selector(row_selector, state="attached", timeout=timeout_ms)
                page.locator(row_selector).first.wait_for(state="visible", timeout=timeout_ms)
                cards = page.locator(row_selector)
                for index in range(cards.count()):
                    card = cards.nth(index)
                    detail_discovered += 1
                    list_url = page.url
                    detail_page = None
                    detail_page_is_current = False
                    card_id = f"page-{pages_scanned}-row-{index + 1}"
                    try:
                        title = _first_text(card, (".job-name", "h3", "h4"))
                        employer = _first_text(card, (".company-name", ".requirement .company-name"))
                        card_id = card.get_attribute("data-id") or card.get_attribute("id") or card_id
                        fold = card.locator(".fold").first
                        if fold.count() and fold.is_visible():
                            fold.click()
                        detail_link = card.locator("a[href]").filter(
                            has_text=re.compile(r"查\s*看")
                        ).first
                        href = (
                            str(detail_link.get_attribute("href") or "").strip()
                            if detail_link.count()
                            else ""
                        )
                        if href:
                            detail_page = context.new_page()
                            detail_page.goto(
                                urljoin(list_url, href),
                                wait_until="domcontentloaded",
                                timeout=timeout_ms,
                            )
                        detail_button = card.locator(detail_button_selector).filter(
                            has_text=re.compile(r"查\s*看")
                        ).first
                        if not detail_button.count():
                            detail_button = card.get_by_text(
                                re.compile(r"查\s*看"), exact=True
                            ).first
                        if detail_page is None and not detail_button.count():
                            raise BrowserCaptureError("CMGB card has no 查看 detail control")
                        if detail_page is None:
                            try:
                                with context.expect_page(timeout=detail_popup_timeout_ms) as detail_info:
                                    detail_button.click()
                                detail_page = detail_info.value
                            except PlaywrightTimeoutError:
                                # Some deployments use an SPA route in the same
                                # tab.  The click has already happened; use the
                                # current page only when its route/content changed.
                                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                                page.wait_for_timeout(detail_render_wait_ms)
                                if page.url == list_url and page.locator(row_selector).count():
                                    raise BrowserCaptureError(
                                        "CMGB 查看 control did not open an official detail route"
                                    )
                                detail_page = page
                                detail_page_is_current = True
                        detail_page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                        detail_page.wait_for_timeout(detail_render_wait_ms)
                        try:
                            detail_page.locator(
                                ".job-duty, .job-introduction, .job-description"
                            ).first.wait_for(
                                state="visible", timeout=min(timeout_ms, 5_000)
                            )
                        except PlaywrightTimeoutError:
                            # Some valid details do not have a prose block; the
                            # field parser will fail closed if the overview is
                            # also insufficient.
                            pass
                        detail_url = detail_page.url
                        detail = extract_cmgb_detail(
                            detail_page,
                            detail_url=detail_url,
                            allowed_hosts=hosts,
                            # A single 国聘 detail occasionally omits the
                            # employer while its official list card still
                            # displays it. The card and detail are the same
                            # first-party record; use that value only as a
                            # controlled fallback, and still fail if both are
                            # absent.
                            require_employer=False,
                        )
                        resolved_employer = str(detail.get("employer") or employer).strip()
                        if not resolved_employer:
                            raise BrowserCaptureError(
                                "CMGB detail and official list card are missing employer"
                            )
                        if not detail_page_is_current:
                            detail_page.close()
                        else:
                            page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
                            page.wait_for_selector(row_selector, timeout=timeout_ms)
                        detail_id = parse_qs(urlparse(detail["detail_url"]).query).get("id", [""])[0].strip()
                        external_id = (
                            f"cmgb-iguopin-{detail_id}"
                            if detail_id
                            else str(card_id).strip()
                        )
                        if external_id in seen:
                            raise BrowserCaptureError(
                                f"duplicate CMGB card identifier: {external_id}"
                            )
                        seen.add(external_id)
                        rows.append({
                            "external_id": external_id,
                            "title": detail["title"] or title,
                            "employer": resolved_employer,
                            "major": detail["major"],
                            "degree": detail["degree"],
                            "location": detail["location"],
                            "headcount": detail["headcount"],
                            "deadline": detail["deadline"],
                            "detail_url": detail["detail_url"],
                            "evidence_url": detail["evidence_url"],
                            "description": detail["description"],
                            "field_evidence": detail["field_evidence"],
                        })
                    except Exception as error:
                        if detail_page is not None and not detail_page_is_current:
                            try:
                                detail_page.close()
                            except Exception:
                                pass
                        failure_records.append(
                            {
                                "page": pages_scanned,
                                "row": index + 1,
                                "card_id": str(card_id),
                                # A detail page can omit the employer even
                                # though its first-party list card has it.
                                # Preserve that fallback for a later
                                # detail-only retry; it is not inferred from
                                # the title or any third-party source.
                                "title": title,
                                "employer": employer,
                                "detail_url": str(detail_page.url) if detail_page is not None else "",
                                "reason": str(error)[:500],
                            }
                        )
                        failed_rows += 1
                        detail_failed += 1
                next_button = page.locator(next_selector).first
                disabled = (
                    not next_button.count()
                    or next_button.get_attribute("disabled") is not None
                    or "disabled" in str(next_button.get_attribute("class") or "").lower()
                    or str(next_button.get_attribute("aria-disabled") or "").lower() == "true"
                )
                if disabled:
                    pagination_complete = True
                    break
                if pages_scanned >= max_pages:
                    break
                next_button.click()
                page.wait_for_timeout(initial_render_wait_ms)
                page.wait_for_selector(row_selector, state="attached", timeout=timeout_ms)
                page.locator(row_selector).first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"CMGB browser page timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CMGB browser capture failed: {error}") from error
    finally:
        # Closing every page covers popup detail pages too. This is essential
        # for a worker that runs all day: leaked renderers eventually make
        # later official SPA captures fail with resource-exhaustion errors.
        if context is not None:
            _close_context_pages(context)
        _close_browser_connection(browser)

    status = "success" if pagination_complete and failed_rows == 0 else "partial"
    payload = {
        "version": 1,
        "status": status,
        "platform_url": target_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": pagination_complete,
            "rows_discovered": detail_discovered,
            "rows_exported": len(rows),
            "failed_rows": failed_rows,
            "detail_discovered": detail_discovered,
            "detail_succeeded": len(rows),
            "detail_failed": detail_failed,
            "failure_records": failure_records,
        },
        "rows": rows,
    }
    return persist_cmgb_browser_capture(
        output=output,
        payload=payload,
        source_id=str(config.get("source_id") or "cmgb-iguopin-browser"),
        adapter_version=str(config.get("adapter_version") or "cmgb-browser-v1"),
    )


def run_cmgb_detail_retry(
    *,
    retry_capture_path: Path | str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: Iterable[str],
    user_agent: str,
    max_age_hours: float | None = 12,
    require_capture_manifest: bool = False,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Retry only the failed official CMGB detail tabs from one frozen scan.

    It intentionally does not revisit the list pages. A retry is allowed only
    for a recent, pagination-complete partial capture whose failure records
    contain first-party detail URLs. The output becomes canonical only when
    every target now has complete official fields.
    """

    hosts = _cmgb_allowed_hosts(allowed_hosts)
    retry_capture = load_cmgb_detail_retry_capture(
        retry_capture_path,
        allowed_hosts=hosts,
        max_age_hours=max_age_hours,
        require_capture_manifest=require_capture_manifest,
    )
    target_url = str(retry_capture["platform_url"])
    _robots_permit(target_url, user_agent=user_agent)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before retrying CMGB details"
        ) from error

    detail_render_wait_ms = max(
        250, min(10_000, int(config.get("detail_render_wait_ms", 1_000)))
    )
    retried_rows: list[dict[str, Any]] = []
    retry_failures: list[dict[str, Any]] = []
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()

            def open_session() -> None:
                """Attach a clean Playwright context for the next detail."""

                nonlocal browser, context
                if cdp_url:
                    browser = playwright.chromium.connect_over_cdp(
                        _resolve_cdp_websocket(cdp_url)
                    )
                    if not browser.contexts:
                        raise BrowserCaptureError(
                            "CMGB CDP browser has no default context"
                        )
                    context = browser.contexts[0]
                    _close_context_pages(context)
                else:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(user_agent=user_agent)

            def reset_session() -> None:
                """Drop a poisoned renderer without touching the capture file."""

                nonlocal browser, context
                if context is not None:
                    _close_context_pages(context)
                # For a connected CDP browser this disconnects Playwright but
                # leaves the dedicated headless service running. For a local
                # browser it also stops the owned process.
                _close_browser_connection(browser)
                browser = None
                context = None
                open_session()

            open_session()

            retry_attempts = max(
                1, min(5, int(config.get("detail_retry_attempts", 3)))
            )
            retry_delay_ms = max(
                0, min(5_000, int(config.get("detail_retry_delay_ms", 500)))
            )
            reconnect_on_crash = bool(
                config.get("detail_retry_reconnect_on_crash", True)
            )
            reconnect_delay_ms = max(
                0,
                min(
                    5_000,
                    int(config.get("detail_retry_reconnect_delay_ms", 750)),
                ),
            )
            for target in retry_capture["retry_targets"]:
                last_error: Exception | None = None
                completed = False
                for attempt in range(1, retry_attempts + 1):
                    if context is None:
                        raise BrowserCaptureError(
                            "CMGB detail retry has no active browser context"
                        )
                    page = context.new_page()
                    try:
                        detail_url = _official_url(
                            target["detail_url"], "retry detail_url", hosts
                        )
                        # Respect the path-specific robots rule before each
                        # direct detail request. This does not attempt to
                        # bypass any restriction; a blocked target remains a
                        # failed target and is not retried.
                        _robots_permit(detail_url, user_agent=user_agent)
                        response = page.goto(
                            detail_url,
                            wait_until="domcontentloaded",
                            timeout=timeout_ms,
                        )
                        if response is not None and response.status >= 400:
                            raise BrowserCaptureError(
                                f"CMGB retry detail returned HTTP {response.status}"
                            )
                        page.wait_for_timeout(detail_render_wait_ms)
                        try:
                            page.locator(
                                ".job-duty, .job-introduction, .job-description"
                            ).first.wait_for(
                                state="visible", timeout=min(timeout_ms, 5_000)
                            )
                        except PlaywrightTimeoutError:
                            # The field extractor is the authoritative
                            # completeness gate for details without a prose
                            # section.
                            pass
                        detail = extract_cmgb_detail(
                            page,
                            detail_url=page.url,
                            allowed_hosts=hosts,
                            require_employer=False,
                        )
                        retried_rows.append(_cmgb_row_from_detail(detail, target=target))
                        completed = True
                        break
                    except Exception as error:
                        last_error = error
                        if attempt < retry_attempts and _detail_retryable(error):
                            if reconnect_on_crash and _detail_retry_needs_session_reset(error):
                                try:
                                    reset_session()
                                except Exception as recovery_error:
                                    last_error = BrowserCaptureError(
                                        "CMGB detail retry could not restore browser "
                                        f"session after {error}: {recovery_error}"
                                    )
                                    break
                                if reconnect_delay_ms:
                                    time.sleep(reconnect_delay_ms / 1000)
                            if retry_delay_ms:
                                time.sleep((retry_delay_ms * attempt) / 1000)
                            continue
                        break
                    finally:
                        try:
                            if not page.is_closed():
                                page.close()
                        except Exception:
                            pass
                if not completed:
                    retry_failures.append(
                        {
                            **target,
                            "reason": (
                                f"{last_error} (attempts={retry_attempts})"
                                if last_error is not None
                                else "detail retry did not run"
                            )[:500],
                        }
                    )
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"CMGB detail retry timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CMGB detail retry failed: {error}") from error
    finally:
        if context is not None:
            _close_context_pages(context)
        _close_browser_connection(browser)

    payload = build_cmgb_detail_retry_payload(
        retry_capture,
        retried_rows=retried_rows,
        retry_failures=retry_failures,
        allowed_hosts=hosts,
    )
    return persist_cmgb_browser_capture(
        output=output,
        payload=payload,
        source_id=str(config.get("source_id") or "cmgb-iguopin-browser"),
        adapter_version=str(config.get("adapter_version") or "cmgb-browser-v1"),
    )
