"""Phase 3 policy contracts shared by ingestion, lifecycle and presentation.

These values are deliberately kept outside JSON metadata so the database can
index and audit the decisions that affect publication.  The module contains
pure helpers only; it does not perform network access or mutate records.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


PHASE3_NAME = "phase3"
PHASE3_BASELINE = "phase532-logical-phase1"
ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")

TRUST_GRADES = frozenset({"A", "B", "C", "D"})
TRUST_GRADE_LABELS = {
    "A": "官方账号、官网或官方公告",
    "B": "权威机构转载并提供官方链接",
    "C": "权威机构转载但未提供官方链接",
    "D": "普通转载、个人信息或未知来源",
}

PUBLICATION_LAYERS = frozenset(
    {
        "exact_match",
        "broad_geoscience_match",
        "incomplete_but_basic_match",
        "source_unavailable",
        "not_public",
    }
)

SOURCE_AVAILABILITY_STATES = frozenset(
    {"available", "fresh", "stale", "source_unavailable", "degraded", "stale_pending_review"}
)

SOURCE_UNAVAILABLE_REASONS = frozenset(
    {
        "robots_denied",
        "http_403",
        "http_412",
        "tls_error",
        "network_error",
        "javascript_session_failed",
        "official_detail_unavailable",
        "attachment_download_failed",
        "page_structure_changed",
        "browser_batch_incomplete",
        "capture_missing",
        "parser_error",
        "source_disabled",
        "temporary_absence",
        "unknown",
    }
)

# ``superseded`` is a lineage state for a duplicate source snapshot.  It is
# not a withdrawal reason and must never be used as evidence that an official
# vacancy was cancelled.  A successor row is the current public candidate;
# the old row remains available for audit and rollback only.
SUPERSEDED_VISIBILITY_POLICY = "historical_snapshot_only"

LEGACY_PUBLICATION_STATUS_TO_LAYER = {
    "student_eligible": "exact_match",
    "unrestricted_eligible": "broad_geoscience_match",
    # Legacy adjacent-major rows remain auditable but are not part of the
    # student corpus. Explicit broad-geoscience wording is classified through
    # ``major_match_mode=broad_geoscience`` instead.
    "similar_eligible": "not_public",
    "pending_evidence": "incomplete_but_basic_match",
    "source_unavailable": "source_unavailable",
}

WITHDRAWAL_REASONS = frozenset(
    {"major_mismatch", "deadline_passed", "official_cancelled", "official_closed", "official_replaced"}
)


def current_asia_shanghai_date() -> str:
    """Return today's date using the student-facing Beijing-time policy."""

    return datetime.now(ASIA_SHANGHAI).date().isoformat()


def normalize_trust_grade(value: Any, default: str = "D") -> str:
    grade = str(value or "").strip().upper()
    return grade if grade in TRUST_GRADES else default


def normalize_publication_layer(value: Any, default: str = "not_public") -> str:
    layer = str(value or "").strip()
    return layer if layer in PUBLICATION_LAYERS else default


def no_deadline_expiry(
    published_date: str | None,
    first_official_evidence_at: str | None,
) -> str | None:
    """Return the Asia/Shanghai calendar date on which an undated row expires."""

    raw = str(published_date or first_official_evidence_at or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw or " " in raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ASIA_SHANGHAI)
            local_date = parsed.astimezone(ASIA_SHANGHAI).date()
        else:
            local_date = date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return None
    return (local_date + timedelta(days=90)).isoformat()


def resolve_working_day_deadline(
    published_date: str | None,
    working_days: int,
    *,
    non_working_dates: set[str] | frozenset[str] | None = None,
    include_start: bool = True,
) -> str | None:
    """Resolve an official relative deadline on the Asia/Shanghai calendar.

    This helper accepts an explicit holiday/non-working-day set. A weekday
    alone is not enough to claim a Chinese recruitment deadline, so callers
    must provide official calendar exceptions when the interval crosses a
    statutory holiday or make-up workday. For short intervals that do not
    cross any exception, an empty set is auditable and deterministic.
    """

    if working_days <= 0:
        return None
    raw = str(published_date or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw or " " in raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ASIA_SHANGHAI)
            current = parsed.astimezone(ASIA_SHANGHAI).date()
        else:
            current = date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return None

    exceptions = {str(item)[:10] for item in (non_working_dates or set())}
    counted = 0
    if not include_start:
        current += timedelta(days=1)
    while counted < working_days:
        if current.weekday() < 5 and current.isoformat() not in exceptions:
            counted += 1
        if counted < working_days:
            current += timedelta(days=1)
    return current.isoformat()


def deadline_is_expired(deadline_date: str | None, no_deadline_expires_at: str | None, as_of: date) -> bool:
    """Apply only explicit deadline or the confirmed 90-day undated rule."""

    deadline = str(deadline_date or "").strip()
    expiry = str(no_deadline_expires_at or "").strip()
    try:
        if deadline:
            return date.fromisoformat(deadline[:10]) < as_of
        if expiry:
            return date.fromisoformat(expiry[:10]) < as_of
    except ValueError:
        return False
    return False


def source_failure_state(reason: str | None) -> str:
    normalized = str(reason or "unknown").strip().lower()
    if normalized in {"page_structure_changed", "parser_error"}:
        return "degraded"
    if normalized in {"temporary_absence", "capture_missing", "browser_batch_incomplete"}:
        return "stale_pending_review"
    return "source_unavailable"


def classify_legacy_withdrawal_reason(reason: str | None) -> str:
    """Classify old free-text withdrawal events without granting permission.

    The result is an audit classification.  Only the five explicit values in
    ``WITHDRAWAL_REASONS`` are allowed to remain as a current ``withdrawn``
    reason; source failures and one-scan absence must be reviewed/restored.
    """

    normalized = str(reason or "").strip().lower()
    if normalized in WITHDRAWAL_REASONS:
        return normalized
    if any(token in normalized for token in ("专业不匹配", "专业不符", "major_mismatch", "major mismatch")):
        return "major_mismatch_candidate"
    if any(token in normalized for token in ("截止", "过期", "deadline", "expired")):
        return "deadline_passed_candidate"
    if any(token in normalized for token in ("取消", "关闭", "替换", "cancel", "closed", "replaced")):
        return "official_closure_candidate"
    if any(
        token in normalized
        for token in (
            "新鲜度",
            "freshness",
            "30 小时",
            "30小时",
            "192 小时",
            "未出现",
            "清单",
            "台账",
            "捕获",
            "capture",
            "source",
            "来源",
            "附件",
            "missing",
        )
    ):
        return "legacy_source_failure"
    return "unknown_manual_review"


def layer_for_legacy_status(
    publication_status: str | None,
    publication_basis: dict[str, Any] | None = None,
    *,
    has_basic_major_evidence: bool = False,
) -> str:
    """Return the Phase 3 layer for a legacy publication status."""

    status = str(publication_status or "").strip()
    if status == "pending_evidence" and not has_basic_major_evidence:
        return "not_public"
    return derive_publication_layer(
        status,
        publication_basis,
        has_basic_major_evidence=has_basic_major_evidence,
    )


def derive_publication_layer(
    publication_status: str,
    publication_basis: dict[str, Any] | None = None,
    *,
    has_basic_major_evidence: bool = False,
) -> str:
    """Map legacy status values to the four independent student layers."""

    status = str(publication_status or "").strip()
    basis = publication_basis if isinstance(publication_basis, dict) else {}
    mode = str(basis.get("major_match_mode") or "").strip()
    if status == "source_unavailable":
        return "source_unavailable"
    if status in {"student_eligible"}:
        if mode == "incomplete_but_basic":
            return "incomplete_but_basic_match"
        if mode in {
            "public_named_or_code",
            "public_unrestricted_rule",
            "broad_geoscience",
            "social_broad_geoscience",
            "social_unrestricted_rule",
            "extended_unrestricted_rule",
        }:
            return "broad_geoscience_match"
        if mode in {
            "social_exact_four_names",
            "social_incomplete_but_basic",
            "extended_role_context_pending",
        }:
            return "incomplete_but_basic_match"
        return "exact_match"
    if status == "unrestricted_eligible":
        return "broad_geoscience_match"
    # Legacy rows with the old ``similar_major`` marker do not carry enough
    # structured context to be promoted safely. Keep them private; newly
    # evaluated rows use ``student_eligible`` plus ``broad_geoscience``.
    if status == "similar_eligible":
        return (
            "broad_geoscience_match"
            if mode == "broad_geoscience"
            else "not_public"
        )
    if status == "pending_evidence" and has_basic_major_evidence:
        return "incomplete_but_basic_match"
    return "not_public"


__all__ = [
    "ASIA_SHANGHAI",
    "current_asia_shanghai_date",
    "PHASE3_BASELINE",
    "PHASE3_NAME",
    "PUBLICATION_LAYERS",
    "LEGACY_PUBLICATION_STATUS_TO_LAYER",
    "SOURCE_AVAILABILITY_STATES",
    "SUPERSEDED_VISIBILITY_POLICY",
    "SOURCE_UNAVAILABLE_REASONS",
    "TRUST_GRADES",
    "TRUST_GRADE_LABELS",
    "WITHDRAWAL_REASONS",
    "deadline_is_expired",
    "classify_legacy_withdrawal_reason",
    "derive_publication_layer",
    "layer_for_legacy_status",
    "no_deadline_expiry",
    "resolve_working_day_deadline",
    "normalize_publication_layer",
    "normalize_trust_grade",
    "source_failure_state",
]
