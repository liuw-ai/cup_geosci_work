"""Read-only browser capture for bounded official 国聘 discovery surfaces.

Employer-owned portals use a small set of visible programme filters.  The
national board uses an explicitly configured, visible keyword vocabulary.
Neither mode calls an undocumented platform API: every discovered card is
reopened on ``www.iguopin.com`` and checked against its own public detail
page before it can enter a capture manifest.
"""

from __future__ import annotations

import re
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from job_hub.browser_capture import (
    BrowserCaptureError,
    _robots_permit,
    launch_playwright_browser,
)
from job_hub.capture_evidence import attach_capture_manifest
from job_hub.cmgb_browser_capture import (
    CmgbBrowserCaptureError,
    _close_browser_connection,
    _close_context_pages,
    _body_text,
    _first_text,
    _scan_metrics,
    extract_cmgb_detail,
    load_cmgb_browser_capture,
    persist_cmgb_browser_capture,
    write_cmgb_capture_failure,
)
from job_hub.cnpc_browser_runner import _resolve_cdp_websocket
from job_hub.contracts import is_http_url


IguopinBrowserCaptureError = CmgbBrowserCaptureError


_DETERMINISTIC_REJECTION_PREFIXES = (
    "CMGB detail is missing required fields:",
    "国聘详情岗位名称与公开搜索卡片不一致",
    "国聘详情招聘单位与公开搜索卡片不一致",
)


def _is_deterministic_rejection(reason: Any) -> bool:
    """Return whether a detail was opened but cannot represent its card row.

    Missing eligibility fields and card/detail identity conflicts are both
    evidence-backed row decisions. They must be retained as rejected records,
    not treated as pagination or access failures that invalidate the whole
    complete scan.
    """

    value = str(reason or "")
    return value.startswith(_DETERMINISTIC_REJECTION_PREFIXES)


def load_iguopin_browser_capture(
    path: Path | str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    max_age_hours: float | None = 30,
    require_complete_scan: bool = True,
    require_capture_manifest: bool = False,
    allow_empty_fields: Iterable[str] = (),
    allow_empty_duplicate_batch: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load a complete 国聘 capture using the established detail contract."""

    return load_cmgb_browser_capture(
        path,
        allowed_hosts=allowed_hosts,
        max_age_hours=max_age_hours,
        require_complete_scan=require_complete_scan,
        require_capture_manifest=require_capture_manifest,
        allow_empty_fields=allow_empty_fields,
        allow_empty_duplicate_batch=allow_empty_duplicate_batch,
        now=now,
    )


def write_iguopin_capture_failure(**kwargs: Any) -> dict[str, Any]:
    """Archive a failed capture without replacing the last complete snapshot."""

    return write_cmgb_capture_failure(**kwargs)


def promote_deterministic_rejections(
    partial_path: Path | str,
    output: Path | str,
    *,
    source_id: str,
    adapter_version: str,
) -> dict[str, Any]:
    """Promote a complete-pagination partial into a success manifest.

    This is intentionally narrow. It is only valid when every failed detail
    has been opened and rejected for a deterministic missing-field reason;
    access errors, timeouts, DOM failures, and unknown reasons remain partial.
    The promoted manifest contains only complete rows, while rejected details
    remain in the scan audit and are never sent to the publication pipeline.
    """

    path = Path(partial_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserCaptureError(f"cannot read partial 国聘 capture: {path}") from error
    if not isinstance(payload, dict) or payload.get("status") != "partial":
        raise BrowserCaptureError("deterministic rejection promotion requires a partial capture")
    scan = payload.get("scan")
    rows = payload.get("rows")
    if not isinstance(scan, dict) or not isinstance(rows, list):
        raise BrowserCaptureError("partial 国聘 capture is missing scan or rows")
    if not bool(scan.get("pagination_complete")):
        raise BrowserCaptureError("cannot promote a capture with incomplete pagination")
    failures = scan.get("failure_records")
    if not isinstance(failures, list) or not failures:
        raise BrowserCaptureError("partial 国聘 capture has no deterministic failure records")
    if any(
        not isinstance(item, dict)
        or not _is_deterministic_rejection(item.get("reason"))
        for item in failures
    ):
        raise BrowserCaptureError(
            "partial 国聘 capture contains access, timeout, or unknown failures"
        )
    if int(scan.get("failed_rows") or 0) != len(failures):
        raise BrowserCaptureError("failure count does not match failure records")
    detail_discovered = int(scan.get("detail_discovered") or 0)
    existing_rejected = scan.get("rejected_records")
    if not isinstance(existing_rejected, list):
        existing_rejected = []
    if detail_discovered != len(rows) + len(failures) + len(existing_rejected):
        raise BrowserCaptureError("detail totals do not account for every discovered row")

    promoted_scan = dict(scan)
    promoted_scan.update(
        {
            "rows_discovered": len(rows),
            "rows_exported": len(rows),
            "failed_rows": 0,
            "rejected_rows": len(existing_rejected) + len(failures),
            "detail_succeeded": detail_discovered,
            "detail_failed": 0,
            "failure_records": [],
            "rejected_records": [*existing_rejected, *failures],
        }
    )
    promoted_scan.pop("failure_reason", None)
    promoted = dict(payload)
    promoted["status"] = "success"
    promoted["scan"] = promoted_scan
    promoted.pop("capture_evidence", None)
    promoted = attach_capture_manifest(
        promoted,
        source_id=source_id,
        adapter_version=adapter_version,
    )
    return persist_cmgb_browser_capture(
        output=output,
        payload=promoted,
        source_id=source_id,
        adapter_version=adapter_version,
    )


def build_iguopin_rejection_repair_payload(
    capture: dict[str, Any],
    *,
    repaired_rows: list[dict[str, Any]],
    repaired_detail_ids: Iterable[str],
    captured_at: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Merge deterministic detail repairs into one frozen complete scan.

    The pagination result is deliberately not regenerated here.  The original
    manifest already proves that every configured keyword reached its final
    page and every detail was opened.  This operation only reopens the
    rejected official detail URLs after an adapter improvement, preserving
    unresolved rejection evidence and refusing an empty repair.
    """

    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in (allowed_hosts or {"www.iguopin.com", "iguopin.com"})
        if str(host).strip()
    }
    if capture.get("status") != "success":
        raise BrowserCaptureError("国聘 rejected-detail repair requires a successful capture")
    scan = capture.get("scan")
    base_rows = capture.get("rows")
    if not isinstance(scan, dict) or not isinstance(base_rows, list):
        raise BrowserCaptureError("国聘 capture is missing scan or rows")
    if not bool(scan.get("pagination_complete")) or int(scan.get("failed_rows") or 0):
        raise BrowserCaptureError("国聘 rejected-detail repair requires a complete pagination pass")
    rejected = scan.get("rejected_records")
    if not isinstance(rejected, list):
        raise BrowserCaptureError("国聘 capture is missing rejected_records")

    target_ids = {str(item).strip() for item in repaired_detail_ids if str(item).strip()}
    if not target_ids:
        raise BrowserCaptureError("国聘 rejected-detail repair has no completed targets")
    known_ids = {
        str(item.get("detail_id") or "").strip()
        for item in rejected
        if isinstance(item, dict)
    }
    if not target_ids.issubset(known_ids):
        raise BrowserCaptureError("国聘 repair completed a detail outside rejected_records")

    normalized_rows = [dict(row) for row in base_rows]
    seen_external_ids = {str(row.get("external_id") or "") for row in normalized_rows}
    normalized_repaired: list[dict[str, Any]] = []
    for index, row in enumerate(repaired_rows, start=1):
        if not isinstance(row, dict):
            raise BrowserCaptureError(f"国聘 repaired row {index} must be an object")
        detail_url = _concrete_iguopin_detail_url(row.get("detail_url"), hosts)
        detail_id = parse_qs(urlparse(detail_url).query).get("id", [""])[0].strip()
        if detail_id not in target_ids:
            raise BrowserCaptureError("国聘 repaired row is outside completed target IDs")
        external_id = _text(row.get("external_id"), f"repaired row {index}.external_id")
        if external_id in seen_external_ids:
            raise BrowserCaptureError(f"duplicate 国聘 repaired external_id: {external_id}")
        seen_external_ids.add(external_id)
        normalized_repaired.append(dict(row, detail_url=detail_url, evidence_url=row.get("evidence_url") or detail_url))

    repaired_id_set = set(target_ids)
    remaining_rejected = [
        item
        for item in rejected
        if not isinstance(item, dict) or str(item.get("detail_id") or "").strip() not in repaired_id_set
    ]
    if not normalized_repaired:
        raise BrowserCaptureError("国聘 rejected-detail repair produced no complete rows")

    merged_rows = [*normalized_rows, *normalized_repaired]
    merged_scan = dict(scan)
    merged_scan.update(
        {
            "rows_discovered": len(merged_rows),
            "rows_exported": len(merged_rows),
            "rejected_rows": len(remaining_rejected),
            "rejected_records": remaining_rejected,
            "detail_succeeded": int(scan.get("detail_discovered") or 0)
            - int(scan.get("detail_failed") or 0),
            "repair_mode": "deterministic_rejected_detail_repair",
            "repaired_detail_ids": sorted(target_ids),
            "repaired_rows": len(normalized_repaired),
        }
    )
    result = {
        **capture,
        "status": "success",
        "captured_at": _text(
            captured_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "captured_at",
        ),
        "repair_of": capture.get("captured_at"),
        "scan": merged_scan,
        "rows": merged_rows,
    }
    _scan_metrics(result, require_complete=True)
    return result


def run_iguopin_rejected_detail_repair(
    *,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: Iterable[str],
    user_agent: str,
    timeout_ms: int = 45_000,
    max_age_hours: float = 30,
) -> dict[str, Any]:
    """Reopen only deterministic rejected details from a complete capture."""

    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in allowed_hosts
        if str(host).strip()
    }
    target_path = Path(output)
    capture = load_iguopin_browser_capture(
        target_path,
        allowed_hosts=hosts,
        max_age_hours=max_age_hours,
        require_complete_scan=True,
        require_capture_manifest=bool(config.get("require_capture_manifest", False)),
    )
    rejected = capture["scan"].get("rejected_records") or []
    targets: dict[str, dict[str, Any]] = {}
    for item in rejected:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        detail_id = str(item.get("detail_id") or "").strip()
        if detail_id and reason.startswith("CMGB detail is missing required fields:"):
            targets.setdefault(detail_id, item)
    if not targets:
        raise BrowserCaptureError("国聘捕获没有可定向重试的确定性拒绝详情")

    # A complete capture can contain hundreds of deterministic rejects. Keep
    # one repair pass bounded so the unattended browser worker can return to
    # other official sources; later passes revisit the remaining IDs.
    repair_limit = max(1, int(config.get("detail_repair_max_targets") or 120))
    targets = dict(list(targets.items())[:repair_limit])

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError("Playwright is not installed") from error

    repair_budget_seconds = max(
        30.0, float(config.get("detail_repair_max_seconds") or 240.0)
    )
    repair_started = time.monotonic()
    attempted_targets = 0
    repaired_rows: list[dict[str, Any]] = []
    repaired_ids: list[str] = []
    prefix = _external_id_prefix(config)
    _robots_permit(str(config.get("browser_url") or capture["platform_url"]), user_agent=user_agent)
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(_resolve_cdp_websocket(cdp_url))
                if not browser.contexts:
                    raise BrowserCaptureError("国聘 CDP browser has no default context")
                context = browser.contexts[0]
                _close_context_pages(context)
            else:
                browser = launch_playwright_browser(playwright, config)
                context = browser.new_context(user_agent=user_agent)
            page = context.new_page()
            wait_ms = max(250, min(10_000, int(config.get("detail_render_wait_ms", 1_000))))
            for detail_id, target in targets.items():
                if time.monotonic() - repair_started >= repair_budget_seconds:
                    break
                attempted_targets += 1
                try:
                    detail_url = _general_detail_url(detail_id, hosts)
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(wait_ms)
                    detail = extract_cmgb_detail(
                        page,
                        detail_url=page.url,
                        allowed_hosts=hosts,
                        require_employer=False,
                        require_title=False,
                        allow_missing_fields=(
                            ("deadline",)
                            if bool(config.get("allow_missing_deadline"))
                            else ()
                        ),
                    )
                    detail = _complete_general_detail_identity(
                        detail,
                        card_title=str(target.get("card_title") or ""),
                        card_employer=str(target.get("card_employer") or ""),
                    )
                    row = _general_verified_row(
                        detail_id=detail_id,
                        seen_detail_ids={
                            str(item.get("external_id") or "").rsplit("-", 1)[-1]
                            for item in capture["rows"]
                        },
                        card_title=str(target.get("card_title") or ""),
                        card_employer=str(target.get("card_employer") or ""),
                        detail=detail,
                        keyword=str(target.get("keyword") or "repair"),
                        external_id_prefix=prefix,
                    )
                    if row is not None:
                        repaired_rows.append(row)
                        repaired_ids.append(detail_id)
                except Exception:
                    # The original rejection remains private; a transient or
                    # still-incomplete detail must not refresh the capture.
                    continue
    finally:
        if context is not None:
            _close_context_pages(context)
        _close_browser_connection(browser)

    if not repaired_rows:
        reason = (
            "国聘定向详情修复本轮没有完成任何记录；"
            f"已尝试 {attempted_targets}/{len(targets)} 条，"
            f"时间预算 {repair_budget_seconds:g} 秒，保留原完整捕获并等待下一轮重试。"
        )
        write_iguopin_capture_failure(
            output=target_path,
            platform_url=str(capture.get("platform_url") or config.get("browser_url") or ""),
            status="partial",
            reason=reason,
            scan={
                "repair_attempted": len(targets),
                "repair_attempted_before_budget": attempted_targets,
                "repair_budget_seconds": repair_budget_seconds,
                "repair_completed": 0,
                "rejected_remaining": len(rejected),
            },
            rows=[],
            source_id=str(config.get("source_id") or "iguopin-general-browser"),
            adapter_version=str(config.get("adapter_version") or "iguopin-general-browser-v1"),
        )
        raise BrowserCaptureError(reason)

    payload = build_iguopin_rejection_repair_payload(
        capture,
        repaired_rows=repaired_rows,
        repaired_detail_ids=repaired_ids,
        allowed_hosts=hosts,
    )
    return persist_cmgb_browser_capture(
        output=target_path,
        payload=payload,
        source_id=str(config.get("source_id") or "iguopin-general-browser"),
        adapter_version=str(config.get("adapter_version") or "iguopin-general-browser-v1"),
    )


def _text(value: Any, field: str) -> str:
    result = " ".join(str(value or "").split()).strip()
    if not result:
        raise BrowserCaptureError(f"{field} must be non-empty")
    return result


def _official_url(value: Any, field: str, hosts: set[str]) -> str:
    result = _text(value, field)
    if not is_http_url(result):
        raise BrowserCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(result).hostname or "").lower().rstrip(".")
    if host not in hosts:
        raise BrowserCaptureError(f"{field} host is not allowlisted: {host}")
    return result


def _concrete_iguopin_detail_url(value: Any, hosts: set[str]) -> str:
    """Require the click target to be a concrete official job-detail page."""

    detail_url = _official_url(value, "detail_url", hosts)
    parsed = urlparse(detail_url)
    detail_id = parse_qs(parsed.query).get("id", [""])[0].strip()
    if "/job/detail" not in parsed.path or not detail_id:
        raise BrowserCaptureError("国聘详情必须是带 id 的官方岗位详情页")
    return detail_url


def _configured_major_filters(config: dict[str, Any]) -> list[dict[str, str]]:
    """Validate the public cascader choices used to bound one source scan."""

    raw_filters = config.get("major_filters")
    if not isinstance(raw_filters, list) or not raw_filters:
        raise BrowserCaptureError("major_filters must be a non-empty list")
    filters: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_filters, start=1):
        if not isinstance(raw, dict):
            raise BrowserCaptureError(f"major_filters[{index}] must be an object")
        parent = _text(raw.get("parent"), f"major_filters[{index}].parent")
        child = _text(raw.get("child"), f"major_filters[{index}].child")
        key = (parent, child)
        if key in seen:
            raise BrowserCaptureError(f"duplicate visible major filter: {parent} / {child}")
        seen.add(key)
        filters.append({"parent": parent, "child": child})
    return filters


def _external_id_prefix(config: dict[str, Any]) -> str:
    prefix = _text(config.get("external_id_prefix"), "external_id_prefix")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,80}", prefix):
        raise BrowserCaptureError("external_id_prefix must use lowercase letters, digits and hyphens")
    return prefix


def _configured_search_keywords(config: dict[str, Any]) -> list[str]:
    """Validate the explicit visible-search vocabulary for the national board."""

    raw_keywords = config.get("search_keywords")
    if not isinstance(raw_keywords, list) or not raw_keywords:
        raise BrowserCaptureError("search_keywords must be a non-empty list")
    keywords: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_keywords, start=1):
        keyword = _text(raw, f"search_keywords[{index}]")
        if len(keyword) > 80:
            raise BrowserCaptureError("search keyword must be at most 80 characters")
        key = re.sub(r"\s+", "", keyword).casefold()
        if key in seen:
            raise BrowserCaptureError(f"duplicate search keyword: {keyword}")
        seen.add(key)
        keywords.append(keyword)
    return keywords


def _keyword_batches(config: dict[str, Any]) -> list[list[str]]:
    """Split a broad national-board scan into independently auditable batches."""

    keywords = _configured_search_keywords(config)
    raw_size = config.get("search_keyword_batch_size")
    if raw_size in (None, "", 0):
        return [keywords]
    try:
        size = int(raw_size)
    except (TypeError, ValueError) as error:
        raise BrowserCaptureError("search_keyword_batch_size must be a positive integer") from error
    if size <= 0:
        raise BrowserCaptureError("search_keyword_batch_size must be a positive integer")
    return [keywords[index : index + size] for index in range(0, len(keywords), size)]


def _configured_known_detail_ids(config: dict[str, Any]) -> set[str]:
    """Validate detail IDs already verified by an earlier keyword batch."""

    raw_ids = config.get("known_detail_ids")
    if raw_ids in (None, ""):
        return set()
    if not isinstance(raw_ids, list):
        raise BrowserCaptureError("known_detail_ids must be a list")
    result: set[str] = set()
    for index, raw_id in enumerate(raw_ids, start=1):
        detail_id = _text(raw_id, f"known_detail_ids[{index}]")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", detail_id):
            raise BrowserCaptureError(
                f"known_detail_ids[{index}] must be a rendered official detail ID"
            )
        result.add(detail_id)
    return result


def detail_ids_from_capture_payload(
    payload: dict[str, Any],
    *,
    external_id_prefix: str,
) -> set[str]:
    """Project verified and deterministic-rejection IDs for the next batch."""

    prefix = f"{str(external_id_prefix).strip()}-"
    result: set[str] = set()
    for row in payload.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        external_id = str(row.get("external_id") or "").strip()
        if not external_id.startswith(prefix):
            continue
        detail_id = external_id[len(prefix) :]
        if re.fullmatch(r"[A-Za-z0-9_-]{6,128}", detail_id):
            result.add(detail_id)
    scan = payload.get("scan")
    if isinstance(scan, dict):
        for rejected in scan.get("rejected_records", []) or []:
            if not isinstance(rejected, dict):
                continue
            detail_id = str(rejected.get("detail_id") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9_-]{6,128}", detail_id):
                result.add(detail_id)
    return result


def merge_iguopin_general_batch_payloads(
    payloads: Iterable[dict[str, Any]],
    *,
    source_id: str,
    adapter_version: str,
) -> dict[str, Any]:
    """Merge successful keyword-batch manifests without publishing partial data."""

    batches = [payload for payload in payloads if isinstance(payload, dict)]
    if not batches:
        raise BrowserCaptureError("国聘关键词批次不能为空")
    rows_by_id: dict[str, dict[str, Any]] = {}
    rejected_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    keyword_scans: list[dict[str, Any]] = []
    pages_scanned = 0
    cards_seen = 0
    platform_url = str(batches[0].get("platform_url") or "").strip()
    for batch_index, payload in enumerate(batches, start=1):
        if payload.get("status") != "success":
            raise BrowserCaptureError(f"国聘关键词批次 {batch_index} 未完整成功")
        scan = payload.get("scan")
        rows = payload.get("rows")
        if not isinstance(scan, dict) or not isinstance(rows, list):
            raise BrowserCaptureError(f"国聘关键词批次 {batch_index} 缺少扫描清单")
        if not bool(scan.get("pagination_complete")):
            raise BrowserCaptureError(f"国聘关键词批次 {batch_index} 分页未完成")
        if int(scan.get("failed_rows") or 0) or int(scan.get("detail_failed") or 0):
            raise BrowserCaptureError(f"国聘关键词批次 {batch_index} 存在详情失败")
        pages_scanned += int(scan.get("pages_scanned") or 0)
        cards_seen += int(scan.get("cards_seen_before_deduplication") or 0)
        keyword_scans.extend(
            item for item in scan.get("keyword_scans", []) if isinstance(item, dict)
        )
        for row in rows:
            external_id = str(row.get("external_id") or "").strip()
            if not external_id:
                raise BrowserCaptureError(f"国聘关键词批次 {batch_index} 包含无编号岗位")
            rows_by_id.setdefault(external_id, dict(row))
        for rejected in scan.get("rejected_records", []) or []:
            if not isinstance(rejected, dict):
                continue
            key = (
                str(rejected.get("detail_id") or ""),
                str(rejected.get("card_title") or ""),
                str(rejected.get("card_employer") or ""),
                str(rejected.get("reason") or ""),
            )
            rejected_by_key.setdefault(key, dict(rejected))

    rows = list(rows_by_id.values())
    rejected_rows = list(rejected_by_key.values())
    detail_total = len(rows) + len(rejected_rows)
    merged = {
        "version": 1,
        "status": "success",
        "platform_url": platform_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": True,
            "rows_discovered": len(rows),
            "rows_exported": len(rows),
            "failed_rows": 0,
            "rejected_rows": len(rejected_rows),
            "detail_discovered": detail_total,
            "detail_succeeded": detail_total,
            "detail_failed": 0,
            "cards_seen_before_deduplication": cards_seen,
            "keyword_scans": keyword_scans,
            "failure_records": [],
            "rejected_records": rejected_rows,
            "batch_count": len(batches),
            "batch_capture_ids": [
                str((payload.get("capture_evidence") or {}).get("capture_id") or "")
                for payload in batches
            ],
        },
        "rows": rows,
    }
    return attach_capture_manifest(
        merged,
        source_id=source_id,
        adapter_version=adapter_version,
    )


_RENDERED_JOB_ID_SCRIPT = """
(card) => {
  const property = Object.keys(card).find((key) => key.startsWith("__reactFiber$"));
  let fiber = property ? card[property] : null;
  const seen = new Set();
  while (fiber && !seen.has(fiber)) {
    seen.add(fiber);
    const job = fiber.memoizedProps && fiber.memoizedProps.job;
    if (job && typeof job === "object") {
      const jobId = String(job.job_id || "").trim();
      if (/^[A-Za-z0-9_-]{6,128}$/.test(jobId)) return jobId;
    }
    fiber = fiber.return;
  }
  return "";
}
"""


def _rendered_card_detail_id(card: Any) -> str:
    """Read only the identity bound to one browser-rendered public card.

    The national board's unauthenticated card has no detail anchor; its visible
    ``申请职位`` control is login-gated.  The rendered React card still contains
    the platform job ID that binds the shown title and employer to the public
    detail route.  This is not a direct platform API call.  The caller opens
    that public route and rejects the row unless both surfaces agree.
    """

    try:
        value = card.evaluate(_RENDERED_JOB_ID_SCRIPT)
    except Exception as error:
        raise BrowserCaptureError(
            "国聘公开职位卡无法读取其浏览器渲染的岗位编号"
        ) from error
    detail_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", detail_id):
        raise BrowserCaptureError("国聘公开职位卡缺少可核验的岗位编号")
    return detail_id


def _normalized_label(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _visible_keyword_search(
    page: Any,
    *,
    keyword: str,
    config: dict[str, Any],
    timeout_ms: int,
) -> bool:
    """Use only the public keyword search input and its rendered button."""

    input_selector = str(
        config.get("keyword_input_selector") or "input[placeholder='请输入关键词搜索']"
    )
    button_selector = str(
        config.get("keyword_search_button_selector") or ".ant-input-search-button"
    )
    search_input = page.locator(input_selector).first
    if not search_input.count():
        raise BrowserCaptureError(f"国聘主站缺少已核验关键词输入框: {input_selector}")
    search_button = page.locator(button_selector).first
    if not search_button.count():
        raise BrowserCaptureError(f"国聘主站缺少已核验搜索按钮: {button_selector}")
    search_input.fill(keyword)
    search_button.click()
    # 国聘刷新结果是异步的； a fixed short sleep turns a valid search into
    # a false DOM-degraded result during peak traffic.  Wait for either the
    # configured card selector or the site's explicit empty-result text, while
    # retaining a hard upper bound so a broken page still fails loudly.
    wait_ms = max(
        500, min(20_000, int(config.get("keyword_render_wait_ms", 6_000)))
    )
    card_selector = str(config.get("card_selector") or ".job-card")
    empty_pattern = str(
        config.get("empty_result_pattern") or r"暂无(?:职位|数据|结果)"
    )
    deadline = time.monotonic() + wait_ms / 1000.0
    while time.monotonic() < deadline:
        if page.locator(card_selector).count():
            return True
        if re.search(empty_pattern, _body_text(page)):
            return True
        page.wait_for_timeout(250)
    return False


def _general_detail_url(detail_id: str, hosts: set[str]) -> str:
    return _concrete_iguopin_detail_url(
        f"https://www.iguopin.com/job/detail?id={detail_id}", hosts
    )


def _wait_for_rendered_detail(page: Any, *, timeout_ms: int, max_wait_ms: int) -> None:
    """Wait for a rendered detail to expose its job-level evidence.

    The main-board detail route is React-rendered. A fixed sleep made a full
    keyword scan take unnecessarily long when the page was already hydrated,
    while a shorter fixed sleep risks reading the shell before the fields are
    present. Poll the same visible labels consumed by ``extract_cmgb_detail``
    and retain the caller's hard page timeout as the upper bound.
    """

    bounded_wait = max(250, min(int(timeout_ms), int(max_wait_ms)))
    deadline = time.monotonic() + bounded_wait / 1000.0
    evidence_markers = ("专业", "学历", "工作地点", "报名截止", "截止时间")
    while time.monotonic() < deadline:
        body = _body_text(page)
        if any(marker in body for marker in evidence_markers):
            return
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        page.wait_for_timeout(min(100, remaining_ms))


def _general_verified_row(
    *,
    detail_id: str,
    seen_detail_ids: set[str],
    card_title: str,
    card_employer: str,
    detail: dict[str, Any],
    keyword: str,
    external_id_prefix: str,
) -> dict[str, Any] | None:
    """Return one auditable national-board row, or suppress an ID duplicate.

    The public main-board card has no anonymous detail anchor, so its rendered
    ID is only a locator.  This boundary prevents that locator from becoming
    unverified data: the visible card and the opened official detail must
    identify the same vacancy before the row is admitted.
    """

    if detail_id in seen_detail_ids:
        return None
    if _normalized_label(detail.get("title")) != _normalized_label(card_title):
        raise BrowserCaptureError("国聘详情岗位名称与公开搜索卡片不一致")
    if _normalized_label(detail.get("employer")) != _normalized_label(card_employer):
        raise BrowserCaptureError("国聘详情招聘单位与公开搜索卡片不一致")
    evidence = dict(detail["field_evidence"])
    evidence["岗位"] = str(detail["title"]).strip()
    evidence["招聘单位"] = str(detail["employer"]).strip()
    evidence["发现关键词"] = keyword
    evidence["公开搜索卡片岗位"] = card_title
    evidence["公开搜索卡片单位"] = card_employer
    evidence["公开搜索卡片身份"] = "rendered_react_job_id"
    seen_detail_ids.add(detail_id)
    return {
        "external_id": f"{external_id_prefix}-{detail_id}",
        "title": detail["title"],
        "employer": detail["employer"],
        "major": detail["major"],
        "degree": detail["degree"],
        "location": detail["location"],
        "headcount": detail["headcount"],
        "deadline": detail["deadline"],
        "detail_url": detail["detail_url"],
        "evidence_url": detail["evidence_url"],
        "description": detail["description"],
        "field_evidence": evidence,
        "published_date": str(detail.get("published_date") or "").strip(),
    }


def _complete_general_detail_identity(
    detail: dict[str, Any], *, card_title: str, card_employer: str
) -> dict[str, Any]:
    """Complete only identity fields from the same official rendered card.

    The main-board detail template occasionally omits its title or employer
    while the search card displays both.  Those values are safe fallbacks for
    identity because the card and detail share the rendered job ID.  All
    eligibility fields remain detail-only and are never inferred here.
    """

    completed = dict(detail)
    evidence = dict(completed.get("field_evidence") or {})
    fallback_fields: list[str] = []
    if not str(completed.get("title") or "").strip() and str(card_title).strip():
        completed["title"] = str(card_title).strip()
        evidence["岗位身份补充"] = "官方主站搜索卡片"
        fallback_fields.append("title")
    if not str(completed.get("employer") or "").strip() and str(card_employer).strip():
        completed["employer"] = str(card_employer).strip()
        evidence["单位身份补充"] = "官方主站搜索卡片"
        fallback_fields.append("employer")
    if fallback_fields:
        evidence["身份补充字段"] = ", ".join(fallback_fields)
        completed["field_evidence"] = evidence
    if not str(completed.get("title") or "").strip():
        raise BrowserCaptureError("CMGB detail and official search card are missing title")
    if not str(completed.get("employer") or "").strip():
        raise BrowserCaptureError(
            "CMGB detail and official search card are missing employer"
        )
    # The detail template can leave identity evidence blank even when the
    # official search card safely supplies the same identity fields. Keep the
    # evidence map aligned so the source adapter cannot overwrite these keys
    # with empty placeholders during its merge.
    evidence["岗位"] = str(completed["title"]).strip()
    evidence["招聘单位"] = str(completed["employer"]).strip()
    completed["field_evidence"] = evidence
    return completed


def _apply_visible_major_filter(
    page: Any,
    *,
    major_filter: dict[str, str],
    config: dict[str, Any],
    timeout_ms: int,
) -> None:
    """Apply a documented rendered filter, never a guessed portal API call."""

    input_selector = str(config.get("major_filter_input_selector") or "#major")
    menu_selector = str(config.get("major_filter_menu_selector") or ".ant-cascader-menu")
    search_pattern = re.compile(
        str(config.get("major_filter_search_pattern") or r"^\s*搜\s*索\s*$")
    )
    major_input = page.locator(input_selector).first
    if not major_input.count():
        raise BrowserCaptureError(
            f"国聘页面缺少已核验的专业筛选控件: {input_selector}"
        )
    major_input.click()
    menus = page.locator(menu_selector)
    menus.first.wait_for(state="visible", timeout=timeout_ms)
    parent = menus.first.get_by_title(major_filter["parent"], exact=True)
    if not parent.count():
        raise BrowserCaptureError("国聘专业筛选缺少一级选项: " + major_filter["parent"])
    parent.hover()
    page.wait_for_timeout(250)
    if menus.count() < 2:
        raise BrowserCaptureError("国聘专业筛选未显示二级选项: " + major_filter["parent"])
    child = menus.nth(1).get_by_title(major_filter["child"], exact=True)
    if not child.count():
        raise BrowserCaptureError(
            "国聘专业筛选缺少二级选项: "
            + major_filter["parent"]
            + " / "
            + major_filter["child"]
        )
    child.click()
    search_button = page.get_by_role("button", name=search_pattern).first
    if not search_button.count():
        raise BrowserCaptureError("国聘专业筛选缺少搜索按钮")
    search_button.click()
    page.wait_for_timeout(
        max(250, min(10_000, int(config.get("filter_render_wait_ms", 1_000))))
    )


def _next_is_disabled(page: Any, selector: str) -> bool:
    button = page.locator(selector).first
    return (
        not button.count()
        or button.get_attribute("disabled") is not None
        or "disabled" in str(button.get_attribute("class") or "").lower()
        or str(button.get_attribute("aria-disabled") or "").lower() == "true"
    )


def run_iguopin_browser_capture(
    *,
    url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: Iterable[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Capture every page for every configured public major filter.

    A successful manifest means all configured filters reached their final
    page and every unique official detail opened successfully.  A failed
    filter, pagination cap, missing detail or DOM change writes only a
    diagnostic archive; it can never replace a previous complete manifest.
    """

    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in allowed_hosts
        if str(host).strip()
    }
    if not hosts:
        raise BrowserCaptureError("allowed_hosts must not be empty")
    target_url = _official_url(url, "browser_url", hosts)
    _robots_permit(target_url, user_agent=user_agent)
    major_filters = _configured_major_filters(config)
    prefix = _external_id_prefix(config)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before enabling 国聘 capture"
        ) from error

    max_pages_per_filter = max(1, int(config.get("max_pages_per_filter", 20)))
    row_selector = str(config.get("row_selector") or ".ant-card")
    next_selector = str(config.get("next_selector") or ".ant-pagination-next")
    detail_button_selector = str(config.get("detail_button_selector") or "button")
    detail_popup_timeout_ms = max(
        1_000, min(timeout_ms, int(config.get("detail_popup_timeout_ms", 5_000)))
    )
    detail_render_wait_ms = max(250, min(10_000, int(config.get("detail_render_wait_ms", 1_000))))
    initial_render_wait_ms = max(
        detail_render_wait_ms,
        min(20_000, int(config.get("initial_render_wait_ms", 8_000))),
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_scanned = 0
    cards_seen = 0
    failed_rows = 0
    rejected_rows: list[dict[str, Any]] = []
    failure_records: list[dict[str, Any]] = []
    filter_scans: list[dict[str, Any]] = []
    all_filters_complete = True
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
                    raise BrowserCaptureError("国聘 CDP browser has no default context")
                context = browser.contexts[0]
                _close_context_pages(context)
            else:
                browser = launch_playwright_browser(playwright, config)
                context = browser.new_context(user_agent=user_agent)

            page = context.new_page()
            for major_filter in major_filters:
                filter_label = f"{major_filter['parent']} / {major_filter['child']}"
                filter_pages = 0
                filter_cards = 0
                filter_complete = False
                page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(initial_render_wait_ms)
                _apply_visible_major_filter(
                    page,
                    major_filter=major_filter,
                    config=config,
                    timeout_ms=timeout_ms,
                )
                while filter_pages < max_pages_per_filter:
                    filter_pages += 1
                    pages_scanned += 1
                    page.wait_for_selector(row_selector, state="attached", timeout=timeout_ms)
                    page.locator(row_selector).first.wait_for(state="visible", timeout=timeout_ms)
                    cards = page.locator(row_selector)
                    for index in range(cards.count()):
                        card = cards.nth(index)
                        cards_seen += 1
                        filter_cards += 1
                        list_url = page.url
                        detail_page = None
                        detail_page_is_current = False
                        detail_url_for_failure = ""
                        card_id = f"{filter_label}-page-{filter_pages}-row-{index + 1}"
                        title = ""
                        employer = ""
                        try:
                            title = _first_text(card, (".job-name", "h3", "h4"))
                            employer = _first_text(
                                card, (".company-name", ".requirement .company-name")
                            )
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
                                raise BrowserCaptureError("国聘职位卡缺少查看详情控件")
                            if detail_page is None:
                                try:
                                    with context.expect_page(timeout=detail_popup_timeout_ms) as detail_info:
                                        detail_button.click()
                                    detail_page = detail_info.value
                                except PlaywrightTimeoutError:
                                    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                                    page.wait_for_timeout(detail_render_wait_ms)
                                    if page.url == list_url and page.locator(row_selector).count():
                                        raise BrowserCaptureError(
                                            "国聘查看控件没有打开官方岗位详情"
                                        )
                                    detail_page = page
                                    detail_page_is_current = True
                            detail_page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                            detail_page.wait_for_timeout(detail_render_wait_ms)
                            detail_url_for_failure = str(detail_page.url)
                            try:
                                detail_page.locator(
                                    ".job-duty, .job-introduction, .job-description"
                                ).first.wait_for(
                                    state="visible", timeout=min(timeout_ms, 5_000)
                                )
                            except PlaywrightTimeoutError:
                                pass
                            detail = extract_cmgb_detail(
                                detail_page,
                                detail_url=detail_page.url,
                                allowed_hosts=hosts,
                                require_employer=False,
                            )
                            detail["detail_url"] = _concrete_iguopin_detail_url(
                                detail["detail_url"], hosts
                            )
                            detail["evidence_url"] = detail["detail_url"]
                            resolved_employer = str(detail.get("employer") or employer).strip()
                            if not resolved_employer:
                                raise BrowserCaptureError(
                                    "国聘详情和职位卡均未提供招聘单位"
                                )
                            detail_id = parse_qs(
                                urlparse(detail["detail_url"]).query
                            ).get("id", [""])[0].strip()
                            if not detail_id:
                                raise BrowserCaptureError("国聘详情缺少岗位唯一编号")
                            external_id = f"{prefix}-{detail_id}"
                            if not detail_page_is_current:
                                detail_page.close()
                                detail_page = None
                            else:
                                page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
                                page.wait_for_selector(row_selector, timeout=timeout_ms)
                                detail_page_is_current = False
                                detail_page = None
                            # A position may advertise more than one target
                            # major and correctly appear under two visible
                            # filters.  It is still one official job, not an
                            # extraction failure or two student-facing rows.
                            if external_id in seen:
                                continue
                            seen.add(external_id)
                            evidence = dict(detail["field_evidence"])
                            evidence["发现筛选"] = filter_label
                            rows.append(
                                {
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
                                    "field_evidence": evidence,
                                }
                            )
                        except Exception as error:
                            if detail_page_is_current:
                                # Restore the list page before examining the
                                # next card. Otherwise one bad same-tab detail
                                # would make every following row a stale-card
                                # failure and falsely empty a complete scan.
                                try:
                                    if page.url != list_url:
                                        page.go_back(
                                            wait_until="domcontentloaded",
                                            timeout=timeout_ms,
                                        )
                                        page.wait_for_selector(
                                            row_selector, timeout=timeout_ms
                                        )
                                except Exception:
                                    pass
                            elif detail_page is not None:
                                try:
                                    detail_page.close()
                                except Exception:
                                    pass
                            failure_records.append(
                                {
                                    "filter": filter_label,
                                    "page": filter_pages,
                                    "row": index + 1,
                                    "card_id": str(card_id),
                                    "title": title,
                                    "employer": employer,
                                    "detail_url": detail_url_for_failure,
                                    "reason": str(error)[:500],
                                }
                            )
                            failed_rows += 1
                    if _next_is_disabled(page, next_selector):
                        filter_complete = True
                        break
                    if filter_pages >= max_pages_per_filter:
                        break
                    page.locator(next_selector).first.click()
                    page.wait_for_timeout(initial_render_wait_ms)
                filter_scans.append(
                    {
                        "filter": filter_label,
                        "pages_scanned": filter_pages,
                        "cards_seen": filter_cards,
                        "pagination_complete": filter_complete,
                    }
                )
                all_filters_complete = all_filters_complete and filter_complete
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"国聘浏览器页面超时: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"国聘浏览器采集失败: {error}") from error
    finally:
        if context is not None:
            _close_context_pages(context)
        _close_browser_connection(browser)

    successful_details = len(rows)
    detail_discovered = successful_details + failed_rows
    payload = {
        "version": 1,
        "status": "success" if all_filters_complete and failed_rows == 0 else "partial",
        "platform_url": target_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": all_filters_complete,
            "rows_discovered": detail_discovered,
            "rows_exported": successful_details,
            "failed_rows": failed_rows,
            "detail_discovered": detail_discovered,
            "detail_succeeded": successful_details,
            "detail_failed": failed_rows,
            "cards_seen_before_deduplication": cards_seen,
            "filter_scans": filter_scans,
            "failure_records": failure_records,
        },
        "rows": rows,
    }
    return persist_cmgb_browser_capture(
        output=output,
        payload=payload,
        source_id=str(config.get("source_id") or "iguopin-browser"),
        adapter_version=str(config.get("adapter_version") or "iguopin-browser-v1"),
    )


def run_iguopin_general_browser_capture(
    *,
    url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: Iterable[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Capture bounded keyword searches from the public 国聘 main board.

    Every configured keyword is searched through the rendered UI, every
    resulting page is visited until its visible final page, and every unique
    card is reopened on its official detail route.  A direct detail is trusted
    only after its title and employer agree with the card that produced it.
    This makes broad discovery usable without letting generic search results
    create anonymous, incomplete or duplicate student vacancies.
    """

    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in allowed_hosts
        if str(host).strip()
    }
    if not hosts:
        raise BrowserCaptureError("allowed_hosts must not be empty")
    target_url = _official_url(url, "browser_url", hosts)
    _robots_permit(target_url, user_agent=user_agent)
    keywords = _configured_search_keywords(config)
    prefix = _external_id_prefix(config)
    if str(config.get("card_identity_mode") or "") != "rendered_react_job_id":
        raise BrowserCaptureError(
            "国聘主站采集必须明确声明 card_identity_mode=rendered_react_job_id"
        )
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before enabling 国聘 capture"
        ) from error

    max_pages_per_keyword = max(1, int(config.get("max_pages_per_keyword", 20)))
    card_selector = str(config.get("card_selector") or ".job-card")
    next_selector = str(config.get("next_selector") or ".ant-pagination-next")
    initial_render_wait_ms = max(
        500, min(20_000, int(config.get("initial_render_wait_ms", 3_000)))
    )
    detail_render_wait_ms = max(
        250, min(10_000, int(config.get("detail_render_wait_ms", 600)))
    )
    keyword_search_attempts = max(
        1, min(4, int(config.get("keyword_search_attempts", 3)))
    )
    rows: list[dict[str, Any]] = []
    seeded_detail_ids = _configured_known_detail_ids(config)
    seen_detail_ids: set[str] = set(seeded_detail_ids)
    seen_failures: set[str] = set()
    pages_scanned = 0
    cards_seen = 0
    duplicate_cards_skipped = 0
    failed_rows = 0
    failure_records: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    keyword_scans: list[dict[str, Any]] = []
    all_keywords_complete = True
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
                    raise BrowserCaptureError("国聘 CDP browser has no default context")
                context = browser.contexts[0]
                _close_context_pages(context)
            else:
                browser = launch_playwright_browser(playwright, config)
                context = browser.new_context(user_agent=user_agent)

            page = context.new_page()
            # Reuse one detail tab for the whole scan. Creating and closing a
            # Chromium page for every card adds substantial overhead on the
            # national board without improving evidence isolation: each
            # navigation still resets the same official detail document.
            detail_page = context.new_page()
            for keyword in keywords:
                keyword_pages = 0
                keyword_cards = 0
                keyword_complete = False
                page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(initial_render_wait_ms)
                search_ready = False
                for search_attempt in range(keyword_search_attempts):
                    if search_attempt:
                        # A public React board can finish loading its shell while
                        # the first search event is still dropped. Reloading the
                        # same official page is a bounded recovery, not a source
                        # change or a bypass of access controls.
                        page.reload(
                            wait_until="domcontentloaded", timeout=timeout_ms
                        )
                        page.wait_for_timeout(initial_render_wait_ms)
                    search_ready = _visible_keyword_search(
                        page,
                        keyword=keyword,
                        config=config,
                        timeout_ms=timeout_ms,
                    )
                    if search_ready:
                        break
                if not search_ready:
                    body = _body_text(page)
                    lowered_body = body.casefold()
                    access_markers = (
                        "访问过于频繁",
                        "安全验证",
                        "验证码",
                        "请求过于频繁",
                        "access denied",
                        "too many requests",
                    )
                    if any(marker.casefold() in lowered_body for marker in access_markers):
                        raise BrowserCaptureError(
                            "国聘主站关键词搜索触发官方访问限制或安全验证"
                        )
                    raise BrowserCaptureError(
                        "国聘主站关键词搜索在有限重试后仍无岗位卡片或已核验空结果提示"
                    )
                try:
                    while keyword_pages < max_pages_per_keyword:
                        cards = page.locator(card_selector)
                        if not cards.count():
                            body = _body_text(page)
                            empty_pattern = str(
                                config.get("empty_result_pattern") or r"暂无(?:职位|数据|结果)"
                            )
                            if re.search(empty_pattern, body):
                                keyword_complete = True
                                break
                            raise BrowserCaptureError(
                                "国聘主站关键词搜索既无岗位卡片也无已核验空结果提示"
                            )
                        cards.first.wait_for(state="visible", timeout=timeout_ms)
                        keyword_pages += 1
                        pages_scanned += 1
                        for index in range(cards.count()):
                            card = cards.nth(index)
                            cards_seen += 1
                            keyword_cards += 1
                            card_title = ""
                            card_employer = ""
                            detail_id = ""
                            failure_key = f"{keyword}-page-{keyword_pages}-row-{index + 1}"
                            try:
                                card_title = _first_text(card, (".job-name", "h3", "h4"))
                                card_employer = _first_text(
                                    card,
                                    (".company-name", ".requirement .company-name"),
                                )
                                detail_id = _rendered_card_detail_id(card)
                                failure_key = detail_id
                                if detail_id in seen_detail_ids:
                                    duplicate_cards_skipped += 1
                                    continue
                                detail_url = _general_detail_url(detail_id, hosts)
                                detail_page.goto(
                                    detail_url,
                                    wait_until="domcontentloaded",
                                    timeout=timeout_ms,
                                )
                                _wait_for_rendered_detail(
                                    detail_page,
                                    timeout_ms=timeout_ms,
                                    max_wait_ms=detail_render_wait_ms,
                                )
                                detail = extract_cmgb_detail(
                                    detail_page,
                                    detail_url=detail_page.url,
                                    allowed_hosts=hosts,
                                    require_employer=False,
                                    require_title=False,
                                    allow_missing_fields=(
                                        ("deadline",)
                                        if bool(config.get("allow_missing_deadline"))
                                        else ()
                                    ),
                                )
                                detail = _complete_general_detail_identity(
                                    detail,
                                    card_title=card_title,
                                    card_employer=card_employer,
                                )
                                if (
                                    bool(config.get("allow_missing_deadline"))
                                    and not str(detail.get("deadline") or "").strip()
                                    and not str(detail.get("published_date") or "").strip()
                                ):
                                    raise BrowserCaptureError(
                                        "国聘滚动招聘详情缺少官方更新日期"
                                    )
                                row = _general_verified_row(
                                    detail_id=detail_id,
                                    seen_detail_ids=seen_detail_ids,
                                    card_title=card_title,
                                    card_employer=card_employer,
                                    detail=detail,
                                    keyword=keyword,
                                    external_id_prefix=prefix,
                                )
                                if row is not None:
                                    rows.append(row)
                            except Exception as error:
                                # A rendered official detail can be complete
                                # enough to prove that the vacancy exists while
                                # still lacking a publishable major/location/
                                # headcount field.  That is a deterministic
                                # eligibility rejection, not an access or
                                # pagination failure; keep it auditable without
                                # allowing the row into the public snapshot.
                                message = str(error)
                                if _is_deterministic_rejection(message):
                                    # A duplicate keyword may expose the same
                                    # rendered job id again. Mark deterministic
                                    # rejections as seen so the audit counts
                                    # one detail decision per vacancy.
                                    if detail_id:
                                        seen_detail_ids.add(detail_id)
                                    rejected_rows.append(
                                        {
                                            "keyword": keyword,
                                            "page": keyword_pages,
                                            "row": index + 1,
                                            "card_title": card_title,
                                            "card_employer": card_employer,
                                            "detail_id": detail_id,
                                            "reason": message[:500],
                                        }
                                    )
                                    continue
                                if failure_key not in seen_detail_ids and failure_key not in seen_failures:
                                    seen_failures.add(failure_key)
                                    failed_rows += 1
                                    failure_records.append(
                                        {
                                            "keyword": keyword,
                                            "page": keyword_pages,
                                            "row": index + 1,
                                            "card_title": card_title,
                                            "card_employer": card_employer,
                                            "detail_id": detail_id,
                                            "reason": str(error)[:500],
                                        }
                                    )
                            finally:
                                # Keep the detail tab alive for the next card;
                                # the surrounding context closes it atomically
                                # when the complete capture or failure exits.
                                pass
                        if _next_is_disabled(page, next_selector):
                            keyword_complete = True
                            break
                        if keyword_pages >= max_pages_per_keyword:
                            break
                        page.locator(next_selector).first.click()
                        page.wait_for_timeout(initial_render_wait_ms)
                except PlaywrightTimeoutError as error:
                    failure_records.append(
                        {
                            "keyword": keyword,
                            "page": keyword_pages,
                            "row": 0,
                            "reason": f"国聘关键词分页超时: {error}"[:500],
                        }
                    )
                    failed_rows += 1
                keyword_scans.append(
                    {
                        "keyword": keyword,
                        "pages_scanned": keyword_pages,
                        "cards_seen": keyword_cards,
                        "pagination_complete": keyword_complete,
                    }
                )
                all_keywords_complete = all_keywords_complete and keyword_complete
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"国聘主站浏览器页面超时: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"国聘主站浏览器采集失败: {error}") from error
    finally:
        if context is not None:
            _close_context_pages(context)
        _close_browser_connection(browser)

    successful_details = len(rows)
    detail_discovered = successful_details + failed_rows + len(rejected_rows)
    payload = {
        "version": 1,
        "status": "success" if all_keywords_complete and failed_rows == 0 else "partial",
        "platform_url": target_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": all_keywords_complete,
            # Only rows with all mandatory fields are exportable.  Deterministic
            # evidence rejections are counted in detail_succeeded/rejected_rows
            # but are intentionally absent from the public row list.
            "rows_discovered": successful_details,
            "rows_exported": successful_details,
            "failed_rows": failed_rows,
            "rejected_rows": len(rejected_rows),
            "detail_discovered": detail_discovered,
            "detail_succeeded": successful_details + len(rejected_rows),
            "detail_failed": failed_rows,
            "cards_seen_before_deduplication": cards_seen,
            "duplicate_cards_skipped": duplicate_cards_skipped,
            "keyword_scans": keyword_scans,
            "failure_records": failure_records,
            "rejected_records": rejected_rows,
            "prevalidated_detail_count": len(seeded_detail_ids),
        },
        "rows": rows,
    }
    return persist_cmgb_browser_capture(
        output=output,
        payload=payload,
        source_id=str(config.get("source_id") or "iguopin-general-browser"),
        adapter_version=str(config.get("adapter_version") or "iguopin-general-browser-v1"),
        allow_empty_duplicate_batch=bool(config.get("allow_empty_duplicate_batch")),
    )


__all__ = [
    "IguopinBrowserCaptureError",
    "load_iguopin_browser_capture",
    "run_iguopin_general_browser_capture",
    "run_iguopin_rejected_detail_repair",
    "build_iguopin_rejection_repair_payload",
    "detail_ids_from_capture_payload",
    "run_iguopin_browser_capture",
    "write_iguopin_capture_failure",
    "promote_deterministic_rejections",
]
