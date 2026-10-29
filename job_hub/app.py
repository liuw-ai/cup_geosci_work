from __future__ import annotations

import hmac
import hashlib
import re
import secrets
from threading import Lock
from time import monotonic
from datetime import date, datetime, timezone
from functools import wraps
from typing import Any, Callable
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from job_hub.audit import audit_database
from job_hub.attachments import (
    AttachmentProcessingError,
    OfficialAttachmentProcessor,
    build_attachment_field_evidence,
)
from job_hub.config import PROJECT_ROOT, Settings, load_project_env
from job_hub.contracts import (
    CANDIDATE_LEAD_STATUSES,
    CANDIDATE_LEAD_TRANSITIONS,
    NATIONAL_RUNTIME_STATUSES,
    ORGANIZATION_ROLES,
    SOURCE_VALIDATION_STAGES,
    is_http_url,
)
from job_hub.coverage import build_coverage_report
from job_hub.expansion_targets import load_effective_job_target_plan
from job_hub.source_funnel import build_source_funnel
from job_hub.source_fallbacks import build_fallback_audit, load_fallback_plan
from job_hub.cnpc_matrix import CnpcMatrixError, build_cnpc_matrix_report
from job_hub.cnpc_browser_capture import (
    CnpcBrowserCaptureError,
    build_cnpc_browser_capture_report,
)
from job_hub.db import Database
from job_hub.discovery import (
    discovery_funnel,
    discovery_source_rows,
    discovery_source_summary,
    load_discovery_source_registry,
)
from job_hub.employers import (
    CATEGORY_DISPLAY_NAMES,
    enrich_job,
    load_employment_landscape,
)
from job_hub.locations import PROVINCES
from job_hub.lead_publication import publish_verified_candidate_lead
from job_hub.matching import CATEGORY_DESCRIPTIONS, CATEGORY_ORDER, DEGREE_ORDER
from job_hub.organizations import (
    load_organization_registry,
    organization_matrix_rows,
    organization_matrix_summary,
)
from job_hub.three_oil_catalog import (
    build_phase4_external_identity_evidence,
    build_three_oil_catalog_audit,
)
from job_hub.three_oil_entry_evidence import (
    load_three_oil_entry_evidence,
    three_oil_entry_evidence_summary,
)


ADMIN_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "reviewer": frozenset(
        {"dashboard.view", "analytics.view", "leads.view", "leads.edit", "leads.publish"}
    ),
    "analyst": frozenset({"dashboard.view", "analytics.view", "leads.view"}),
}
ANALYTICS_BOT_PATTERN = re.compile(
    r"bot|spider|crawler|slurp|bingpreview|headless|curl|wget|python-requests|scrapy|facebookexternalhit|bytespider",
    re.IGNORECASE,
)
ANALYTICS_VISITOR_COOKIE = "jh_analytics_vid"
ANALYTICS_SESSION_COOKIE = "jh_analytics_sid"
from job_hub.national_sources import (
    load_national_source_matrix,
    national_source_matrix_rows,
    national_source_matrix_summary,
)
from job_hub.national_probes import (
    load_national_source_probes,
    national_source_probe_summary,
)
from job_hub.entry_probes import (
    load_national_entry_probe_run,
    load_national_entry_targets,
    national_entry_probe_summary,
)
from job_hub.pipeline import JobPipeline
from job_hub.phase3_policy import PUBLICATION_LAYERS
from job_hub.profiles import (
    StudentProfile,
    annotate_profile_match,
    get_student_profile,
    list_student_profiles,
)
from job_hub.source_targets import REQUIRED_ROLES
from job_hub.student_discovery import (
    DISCOVERY_SCOPE,
    build_student_discovery_jobs,
    discovery_missing_fields,
)
from job_hub.source_validation import (
    load_source_validation_registry,
    source_validation_matrix_rows,
    source_validation_summary,
)
from job_hub.backups import DatabaseBackupManager
from job_hub.government_artifacts import (
    load_government_artifact_manifest,
    project_government_artifact_manifest,
)
from job_hub.operations import backup_health_payload, build_production_readiness
from job_hub.government_positions import (
    load_position_registry,
    source_activation_tasks,
    upcoming_position_records,
)
from job_hub.government_revalidation import (
    requires_manual_government_evidence_confirmation,
    requires_preopening_evidence_confirmation,
)
from job_hub.reports import build_daily_report, local_today, publish_daily_report
from job_hub.sources import RawPosting
from job_hub.sinopec import load_sinopec_capture, sinopec_capture_summary


_PRIVATE_JOB_API_FIELDS = {
    "third_party_source_id",
    "third_party_discovery_url",
    "third_party_batch_id",
    "third_party_discovery_sha256",
    "third_party_table_url",
    "table_sha256",
    "table_sheet_name",
    "table_row_number",
}
_PRIVATE_EVIDENCE_KEYS = {
    "third_party_discovery_url",
    "third_party_table_url",
    "third_party_source_id",
    "third_party_batch_id",
    "third_party_discovery_sha256",
    "第三方发现页",
    "第三方表格链接",
    "第三方来源",
    "华图发现页",
    "官方详情状态",
}


ADMIN_LEAD_STATUS_LABELS = {
    "candidate": "待回溯官方来源",
    "official_url_found": "已找到官方原文",
    "need_review": "待人工审核",
    "official_content_verified": "官方内容已核验",
    "published": "已发布",
    "rejected": "已驳回",
    "expired": "候选已过期",
}
ADMIN_DOMAIN_STATUS_LABELS = {
    "unverified": "未核验",
    "registered_source_match": "匹配已登记官方来源",
    "manual_review_approved": "人工批准官方域名",
    "source_domain_mismatch": "与登记官方来源不匹配",
    "manual_review_required": "需要人工确认",
}
ADMIN_THREE_OIL_ENTRY_STATUS_LABELS = {
    "unit_specific_entry_verified": "单位独立入口已核验",
    "group_portal_unit_display_observed": "集团入口已观察到单位展示",
    "group_portal_registered": "集团共享入口已登记",
    "official_site_checked_no_unit_entry_found": "已查官方站，未找到独立入口",
    "group_portal_not_revalidated": "集团入口待复核",
    "source_unavailable": "官方来源暂时不可用",
}


def _admin_permissions(role: str | None) -> frozenset[str]:
    return ADMIN_ROLE_PERMISSIONS.get(str(role or ""), frozenset())


def _analytics_request_allowed() -> bool:
    """Count only public HTML navigations, never admin/API/assets/bots."""

    if session.get("admin_user_id") is not None:
        return False
    if request.method not in {"GET", "HEAD"}:
        return False
    path = request.path or "/"
    if path.startswith(("/admin", "/api", "/static")):
        return False
    if path in {"/healthz", "/version", "/favicon.ico", "/robots.txt"}:
        return False
    user_agent = request.headers.get("User-Agent", "")
    if ANALYTICS_BOT_PATTERN.search(user_agent):
        return False
    accept = request.headers.get("Accept", "")
    return not accept or "text/html" in accept or "*/*" in accept


def _analytics_digest(settings: Settings, day: str, purpose: str, value: str) -> str:
    message = f"{purpose}:{day}:{value}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _student_api_job(job: dict[str, Any]) -> dict[str, Any]:
    """Return the student API view without private discovery evidence."""

    public = {
        key: value for key, value in job.items() if key not in _PRIVATE_JOB_API_FIELDS
    }
    source_health_state = public.get("source_availability_status")
    # Source-health and recovery reasons are operational audit data.  Keep
    # them in the database and admin views, but do not expose them through the
    # student-facing API.  A source-unavailable layer remains eligible under
    # the lifecycle policy; its public record should look like a normal job
    # and rely on the retained official links for application verification.
    for key in (
        "source_availability_status",
        "source_availability_status_label",
        "source_unavailable_reason",
    ):
        public.pop(key, None)
    if source_health_state in {
        "source_unavailable",
        "degraded",
        "stale_pending_review",
    } or public.get("publication_layer") == "source_unavailable":
        for key in (
            "publication_layer",
            "publication_layer_label",
            "publication_status_label",
        ):
            public.pop(key, None)
    if public.get("publication_status") == "source_unavailable":
        public.pop("publication_status", None)
    evidence = public.get("field_evidence")
    if isinstance(evidence, dict):
        public["field_evidence"] = {
            key: value
            for key, value in evidence.items()
            if key not in _PRIVATE_EVIDENCE_KEYS
        }
    return public


def create_app(settings: Settings | None = None) -> Flask:
    if settings is None:
        load_project_env()
        settings = Settings.from_env()
    settings.ensure_runtime_paths()
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    # The administrator workbench is deliberately separate from the public
    # student UI. Keep its browser session resistant to script access and
    # cross-site form submission while allowing local HTTP acceptance tests.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.base_url.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=8 * 60 * 60,
        SESSION_REFRESH_EACH_REQUEST=True,
    )
    app.json.ensure_ascii = False
    app.extensions["settings"] = settings
    app.extensions["database"] = database
    app.extensions["pipeline"] = pipeline
    app.extensions["attachment_processor"] = OfficialAttachmentProcessor(settings, database)

    # The deployment environment supplies a password hash. Local review
    # instances may omit it, in which case the login stays unavailable rather
    # than silently falling back to the machine API token.
    if settings.admin_password_hash:
        database.upsert_admin_user(
            settings.admin_username,
            settings.admin_password_hash,
            settings.admin_role,
        )

    # Cache only data that has already passed the canonical student-facing
    # visibility filter.  This is process-local by design: a failed process
    # cannot leak a cache entry across restarts, and a bounded expiry makes
    # later corrections visible without a deployment.
    public_report_cache: dict[str, tuple[float, dict[str, Any], bool]] = {}
    public_report_cache_lock = Lock()

    def current_admin_user() -> dict[str, Any] | None:
        user_id = session.get("admin_user_id")
        if not isinstance(user_id, int):
            return None
        user = database.get_admin_user(user_id)
        if not user or not bool(user.get("enabled")):
            return None
        return user

    def admin_can(permission: str) -> bool:
        user = current_admin_user()
        permissions = _admin_permissions(user.get("role") if user else None)
        return "*" in permissions or permission in permissions

    @app.before_request
    def collect_public_analytics() -> None:
        if not _analytics_request_allowed():
            return
        day = local_today(settings).isoformat()
        visitor_cookie = request.cookies.get(ANALYTICS_VISITOR_COOKIE, "").strip()
        session_cookie = request.cookies.get(ANALYTICS_SESSION_COOKIE, "").strip()
        if not visitor_cookie:
            visitor_cookie = secrets.token_urlsafe(24)
        if not session_cookie:
            session_cookie = secrets.token_urlsafe(24)
        g.analytics_pending = {
            "day": day,
            "visitor_key": _analytics_digest(settings, day, "visitor", visitor_cookie),
            "session_key": _analytics_digest(settings, day, "session", session_cookie),
            "visitor_cookie": visitor_cookie,
            "session_cookie": session_cookie,
            "set_visitor_cookie": not bool(request.cookies.get(ANALYTICS_VISITOR_COOKIE)),
            "set_session_cookie": not bool(request.cookies.get(ANALYTICS_SESSION_COOKIE)),
        }

    @app.after_request
    def set_analytics_cookie(response: Any) -> Any:
        pending = getattr(g, "analytics_pending", None)
        if pending and response.status_code < 400 and response.mimetype == "text/html":
            database.record_page_view(
                day=pending["day"],
                visitor_key=pending["visitor_key"],
                session_key=pending["session_key"],
            )
        if pending and response.status_code < 400 and response.mimetype == "text/html":
            if pending["set_visitor_cookie"]:
                response.set_cookie(
                    ANALYTICS_VISITOR_COOKIE,
                    pending["visitor_cookie"],
                    max_age=max(86_400, settings.analytics_cookie_max_age_seconds),
                    httponly=True,
                    secure=settings.base_url.startswith("https://"),
                    samesite="Lax",
                )
            if pending["set_session_cookie"]:
                response.set_cookie(
                    ANALYTICS_SESSION_COOKIE,
                    pending["session_cookie"],
                    max_age=settings.analytics_cookie_max_age_seconds,
                    httponly=True,
                    secure=settings.base_url.startswith("https://"),
                    samesite="Lax",
                )
        return response

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "site_name": settings.site_name,
            "site_attribution": settings.site_attribution,
            "category_order": CATEGORY_ORDER,
            "category_descriptions": CATEGORY_DESCRIPTIONS,
            "category_display_names": CATEGORY_DISPLAY_NAMES,
            "admin_user": current_admin_user(),
            "admin_can": admin_can,
            "degree_order": DEGREE_ORDER,
            "provinces": PROVINCES,
            "student_profiles": list_student_profiles(),
            "active_profile_id": request.args.get("profile", "").strip(),
        }

    @app.template_filter("cn_date")
    def cn_date(value: str | None) -> str:
        if not value:
            return "未注明"
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return value
        return f"{parsed.year}年{parsed.month}月{parsed.day}日"

    @app.template_filter("category_label")
    def category_label(value: str | None) -> str:
        """Render precise student-facing category wording while keeping keys stable."""
        text = str(value or "")
        return CATEGORY_DISPLAY_NAMES.get(text, text)

    @app.template_filter("local_timestamp_date")
    def local_timestamp_date(value: str | None) -> str:
        """Render internally stored UTC timestamps as dates in the site timezone."""
        if not value:
            return "未注明"
        raw = str(value)
        if "T" not in raw:
            return cn_date(raw)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            local_date = parsed.astimezone(ZoneInfo(settings.timezone)).date()
        except ValueError:
            return raw
        return f"{local_date.year}年{local_date.month}月{local_date.day}日"

    @app.template_filter("deadline_status")
    def deadline_status(value: str | None) -> str:
        if not value:
            return "截止日期未注明"
        try:
            days = (date.fromisoformat(value) - local_today(settings)).days
        except ValueError:
            return "截止日期待核验"
        if days < 0:
            return "已截止"
        if days == 0:
            return "今日截止"
        if days == 1:
            return "明日截止"
        if days <= 7:
            return f"{days} 天后截止"
        return f"{days} 天后截止"

    @app.after_request
    def add_security_headers(response: Any) -> Any:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    def report_for_display(report: dict[str, Any]) -> dict[str, Any]:
        """Render only records that remain safe for student-facing display.

        The stored report stays immutable for audit purposes. A later evidence
        correction must still be able to withdraw an out-of-scope job from old
        public report pages.
        """
        displayed = dict(report)
        business_date = local_today(settings).isoformat()
        report_items = {
            key: list(report.get(key, []))
            for key in ("new_jobs", "updated_jobs", "deadline_jobs")
        }
        visible_by_id = database.find_public_jobs(
            (
                int(item["id"])
                for items in report_items.values()
                for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            ),
            as_of_date=business_date,
        )
        for key in ("new_jobs", "updated_jobs", "deadline_jobs"):
            displayed[key] = [
                visible_by_id[int(item["id"])]
                for item in report_items[key]
                if isinstance(item, dict)
                and isinstance(item.get("id"), int)
                and int(item["id"]) in visible_by_id
            ]
        stats = dict(report.get("stats", {}))
        stats.update(
            {
                "new": len(displayed["new_jobs"]),
                "updated": len(displayed["updated_jobs"]),
                "deadline_soon": len(displayed["deadline_jobs"]),
                "open_total": database.count_open_jobs(as_of_date=business_date),
            }
        )
        displayed["stats"] = stats
        return displayed

    def active_report() -> tuple[dict[str, Any], bool]:
        report = database.latest_daily_report()
        today = local_today(settings).isoformat()
        # A frozen report is immutable, but the homepage must not keep showing
        # yesterday's date when today's 20:00 publication has not happened yet.
        # Build an explicit preview for the local calendar day and keep the
        # older snapshot available through /daily/latest and its dated URL.
        is_preview = report is None or str(report.get("report_date")) != today
        cache_key = f"{today}:{'preview' if is_preview else 'frozen'}"
        cached = public_report_cache.get(cache_key)
        if cached is not None and cached[0] > monotonic():
            return cached[1], cached[2]

        # Coalesce simultaneous first requests. Without this, each Gunicorn
        # thread can start the same expensive preview reconciliation.
        with public_report_cache_lock:
            cached = public_report_cache.get(cache_key)
            if cached is not None and cached[0] > monotonic():
                return cached[1], cached[2]
            source_report = (
                build_daily_report(database, settings, local_today(settings))
                if is_preview
                else report
            )
            displayed = report_for_display(source_report)
            if settings.public_report_cache_seconds > 0:
                public_report_cache.clear()
                public_report_cache[cache_key] = (
                    monotonic() + settings.public_report_cache_seconds,
                    displayed,
                    is_preview,
                )
            return displayed, is_preview

    def requested_profile() -> StudentProfile | None:
        profile_id = request.args.get("profile", "").strip()
        profile = get_student_profile(profile_id)
        if profile_id and profile is None:
            abort(404)
        return profile

    def upcoming_government_positions() -> list[dict[str, Any]]:
        """Load only officially evidenced government rows opening after today."""
        path = settings.government_position_registry_path
        if path is None:
            return []
        try:
            registry = load_position_registry(path)
            artifact_manifest = None
            artifact_manifest_available = settings.government_artifact_manifest_path is None
            if settings.government_artifact_manifest_path is not None:
                artifact_manifest = load_government_artifact_manifest(
                    settings.government_artifact_manifest_path
                )
                artifact_manifest = project_government_artifact_manifest(
                    database, artifact_manifest, registry=registry
                )
                artifact_manifest_available = True
            verifications = {
                str(item["source_id"]): item
                for item in database.list_government_source_verifications()
            }
            # A future batch configured for automatic verification must not be
            # published from its static reviewed ledger before the opening
            # instant.  The worker deliberately performs zero requests before
            # that instant; only an explicit prior verification event may make
            # the planning row visible here.
            preopening_confirmation_source_ids = {
                str(source["id"])
                for source in database.list_sources()
                if requires_preopening_evidence_confirmation(source)
            }
            return upcoming_position_records(
                registry,
                today=local_today(settings).isoformat(),
                max_age_hours=settings.government_position_max_age_hours,
                source_verifications=verifications,
                manual_confirmation_source_ids={
                    str(source["id"])
                    for source in database.list_sources()
                    if requires_manual_government_evidence_confirmation(source)
                } | preopening_confirmation_source_ids,
                artifact_manifest=artifact_manifest,
                artifact_manifest_available=artifact_manifest_available,
                now=datetime.now(timezone.utc),
            )
        except (OSError, ValueError, KeyError):
            # A broken or stale ledger must not break the public home page.
            return []

    def list_display_jobs(
        *,
        page: int,
        category: str | None,
        degree: str | None,
        province: str | None,
        relevance_band: str | None,
        query: str | None,
        profile: StudentProfile | None,
        match_filter: str,
        publication_layer: str | None = None,
        scope: str = "current",
    ) -> tuple[list[dict[str, Any]], int]:
        business_date = local_today(settings).isoformat()
        if scope == "official_pending":
            records, total = database.list_official_pending_jobs(
                category=category,
                degree=degree,
                province=province,
                relevance_band=relevance_band,
                publication_layer=publication_layer,
                q=query,
                page=page,
                as_of_date=business_date,
            )
            return [annotate_official_pending_job(job) for job in records], total
        if scope == DISCOVERY_SCOPE:
            # Discovery is a separate read projection. It may include official
            # rows whose qualification/degree/location evidence is incomplete,
            # but it never mutates publication state or the strict current set.
            candidates, _ = database.list_jobs(
                category=None,
                degree=None,
                province=None,
                relevance_band=None,
                q=None,
                page_size=None,
                only_open=False,
                student_visible=False,
                as_of_date=business_date,
            )
            records, _counts = build_student_discovery_jobs(
                candidates,
                as_of_date=business_date,
                category=category,
                degree=degree,
                province=province,
                relevance_band=relevance_band,
                query=query,
                publication_layer=publication_layer,
                artifact_candidates=database.list_artifact_job_candidates(
                    review_status="official_content_verified", limit=500
                ),
            )
            records = [annotate_student_discovery_job(job) for job in records]
            if profile is not None:
                records = [annotate_profile_match(job, profile) for job in records]
                if match_filter:
                    # A discovery row without a persisted qualification can
                    # only be a review item for a selected student profile.
                    allowed = {match_filter}
                    if match_filter == "review":
                        allowed.add("not_recommended")
                    records = [
                        job
                        for job in records
                        if job.get("profile_match", {}).get("level") in allowed
                    ]
            page_size = 20
            start = (page - 1) * page_size
            return records[start : start + page_size], len(records)
        if profile is None:
            return database.list_jobs(
                category=category,
                degree=degree,
                province=province,
                relevance_band=relevance_band,
                publication_layer=publication_layer,
                q=query,
                page=page,
                as_of_date=business_date,
            )

        # A profile match requires the raw announcement as well as derived tags.
        # The source registry bounds the public corpus, and no student data is kept.
        candidates, _ = database.list_jobs(
            category=category,
            province=province,
            relevance_band=relevance_band,
            publication_layer=publication_layer,
            q=query,
            page_size=None,
            as_of_date=business_date,
        )
        annotated = [annotate_profile_match(job, profile) for job in candidates]
        visible = [
            job
            for job in annotated
            if job["profile_match"]["level"] != "not_recommended"
        ]
        if match_filter:
            visible = [
                job
                for job in visible
                if job["profile_match"]["level"] == match_filter
            ]
        visible.sort(
            key=lambda job: (
                -int(job["profile_match"]["priority"]),
                -int(job["relevance_score"]),
                job.get("deadline_date") or "9999-12-31",
                job.get("updated_at") or "",
            )
        )
        page_size = 20
        start = (page - 1) * page_size
        return visible[start : start + page_size], len(visible)

    def annotate_official_pending_job(job: dict[str, Any]) -> dict[str, Any]:
        """Add an explicit missing-field checklist to the private browse view.

        Pending rows are official-source observations, not student-eligible
        vacancies.  The checklist is intentionally derived from row-bound
        evidence only; it must never turn a title keyword into a qualification.
        """
        annotated = dict(job)
        evidence = job.get("field_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        nested = evidence.get("fields")
        if not isinstance(nested, dict):
            nested = {}
        def has_value(*keys: str) -> bool:
            return any(
                str(evidence.get(key) or nested.get(key) or "").strip()
                for key in keys
            )

        missing: list[str] = []
        if not has_value("专业范围", "专业要求", "major"):
            missing.append("专业要求")
        if not has_value("学历要求", "学历", "degree", "面向对象"):
            missing.append("学历要求")
        if not str(job.get("location") or "").strip() and not has_value(
            "工作地点", "官方工作地点", "location"
        ):
            missing.append("工作地点")
        if not str(job.get("deadline_date") or "").strip() and not has_value(
            "截止日期", "报名截止", "报名截止时间", "截止时间", "deadline"
        ):
            missing.append("截止日期")
        annotated["pending_missing_fields"] = missing
        basis = job.get("publication_basis")
        annotated["pending_gate_reason"] = (
            str(basis.get("reason") or "").strip()
            if isinstance(basis, dict)
            else ""
        )
        return annotated

    def annotate_student_discovery_job(job: dict[str, Any]) -> dict[str, Any]:
        """Add the explicit warning fields used by the discovery UI/API."""
        annotated = dict(job)
        annotated["discovery_missing_fields"] = discovery_missing_fields(job)
        if annotated.get("discovery_status") == "official_incomplete":
            annotated["discovery_label"] = "官方信息不完整，需核验"
        return annotated

    def find_student_discovery_job(job_id: int) -> dict[str, Any] | None:
        candidates, _ = database.list_jobs(
            page_size=None,
            only_open=False,
            student_visible=False,
            as_of_date=local_today(settings).isoformat(),
        )
        records, _counts = build_student_discovery_jobs(
            candidates,
            as_of_date=local_today(settings).isoformat(),
            artifact_candidates=database.list_artifact_job_candidates(
                review_status="official_content_verified", limit=500
            ),
        )
        for record in records:
            if int(record.get("id") or 0) == int(job_id):
                return annotate_student_discovery_job(record)
        return None

    @app.get("/")
    def home() -> str:
        report, is_preview = active_report()
        business_date = local_today(settings).isoformat()
        featured = sorted(
            report["new_jobs"] + report["updated_jobs"],
            key=lambda item: (-item["relevance_score"], item["deadline_date"] or "9999-12-31"),
        )[:6]
        if not featured:
            featured, _ = database.list_jobs(
                page_size=6, as_of_date=business_date
            )
        return render_template(
            "home.html",
            report=report,
            is_preview=is_preview,
            live_open_total=database.count_open_jobs(
                as_of_date=business_date
            ),
            as_of_date=business_date,
            featured=featured,
            categories=database.list_categories(
                as_of_date=business_date
            ),
            report_dates=database.list_report_dates(14),
            upcoming_positions=upcoming_government_positions(),
        )

    @app.get("/government/upcoming")
    def upcoming_government() -> str:
        """Show verified official government vacancies before registration opens."""
        return render_template(
            "government_upcoming.html",
            positions=upcoming_government_positions(),
        )

    @app.get("/daily/latest")
    def latest_daily() -> Any:
        report = database.latest_daily_report()
        if report is None:
            return home()
        return render_template(
            "daily.html",
            report=report_for_display(report),
            is_preview=False,
            report_dates=database.list_report_dates(30),
        )

    @app.get("/daily/<report_date>")
    def daily_report(report_date: str) -> str:
        try:
            requested = date.fromisoformat(report_date)
        except ValueError:
            abort(404)
        report = database.get_daily_report(report_date)
        is_preview = False
        if report is None and requested == local_today(settings):
            report = build_daily_report(database, settings, requested)
            is_preview = True
        if report is None:
            abort(404)
        return render_template(
            "daily.html",
            report=report_for_display(report),
            is_preview=is_preview,
            report_dates=database.list_report_dates(30),
        )

    @app.get("/jobs")
    def jobs() -> str:
        # Flask returns ``None`` when a typed query parameter cannot be
        # converted. Treat malformed pagination as the first page instead of
        # turning a harmless public request into a 500 response.
        page = max(request.args.get("page", 1, type=int) or 1, 1)
        category = request.args.get("category", "").strip() or None
        degree = request.args.get("degree", "").strip() or None
        province = request.args.get("province", "").strip() or None
        if province and province not in PROVINCES:
            abort(404)
        relevance_band = request.args.get("relevance", "").strip() or None
        publication_layer = request.args.get("layer", "").strip() or None
        if publication_layer not in {"", *PUBLICATION_LAYERS - {"not_public"}}:
            publication_layer = None
        query = request.args.get("q", "").strip() or None
        scope = request.args.get("scope", "").strip()
        if scope not in {"", "official_pending", DISCOVERY_SCOPE}:
            scope = ""
        official_pending = scope == "official_pending"
        student_discovery = scope == DISCOVERY_SCOPE
        business_date = local_today(settings).isoformat()
        profile = requested_profile()
        match_filter = request.args.get("match", "").strip()
        if match_filter not in {"", "explicit", "review"}:
            match_filter = ""
        records, total = list_display_jobs(
            page=page,
            category=category,
            degree=degree if profile is None else None,
            province=province,
            relevance_band=relevance_band,
            query=query,
            profile=profile,
            match_filter=match_filter,
            publication_layer=publication_layer,
            scope=scope or "current",
        )
        page_size = 20
        page_count = max((total + page_size - 1) // page_size, 1)
        if page > page_count and total:
            abort(404)
        return render_template(
            "jobs.html",
            jobs=records,
            total=total,
            page=page,
            page_count=page_count,
            selected={
                "category": category or "",
                "degree": (degree or "") if profile is None else "",
                "province": province or "",
                "relevance": relevance_band or "",
                "q": query or "",
                "profile": profile.id if profile else "",
                "match": match_filter,
                "layer": publication_layer or "",
                "scope": scope,
            },
            categories=database.list_categories(
                as_of_date=business_date
            ),
            profile=profile,
            official_pending=official_pending,
            student_discovery=student_discovery,
            as_of_date=business_date,
        )

    @app.get("/jobs/<int:job_id>")
    def job_detail(job_id: int) -> str:
        scope = request.args.get("scope", "").strip()
        official_pending = scope == "official_pending"
        student_discovery = scope == DISCOVERY_SCOPE
        if official_pending:
            job = database.find_official_pending_job(
                job_id, as_of_date=local_today(settings).isoformat()
            )
        elif student_discovery:
            job = find_student_discovery_job(job_id)
        else:
            job = database.find_public_job(
                job_id, as_of_date=local_today(settings).isoformat()
            )
        if job is None:
            abort(404)
        profile = requested_profile()
        if official_pending:
            job = annotate_official_pending_job(job)
        return render_template(
            "job_detail.html",
            job=annotate_profile_match(job, profile) if not official_pending else job,
            profile=profile if not official_pending else None,
            official_pending=official_pending,
            student_discovery=student_discovery,
        )

    @app.get("/jobs/discovery-artifact/<int:candidate_id>")
    def student_discovery_artifact_detail(candidate_id: int) -> str:
        """Render a verified government attachment row without a jobs id."""
        job = find_student_discovery_job(-abs(int(candidate_id)))
        if job is None or not job.get("discovery_virtual"):
            abort(404)
        profile = requested_profile()
        return render_template(
            "job_detail.html",
            job=annotate_profile_match(job, profile),
            profile=profile,
            official_pending=False,
            student_discovery=True,
        )

    @app.get("/about")
    def about() -> str:
        source_health = {
            item["source_id"]: item for item in database.list_source_health()
        }
        latest_runs = {
            item["source_id"]: item for item in database.list_latest_crawl_runs()
        }
        return render_template(
            "about.html",
            sources=database.list_sources(),
            failures=database.recent_crawl_failures(),
            coverage=build_coverage_report(
                database,
                snapshot_date=local_today(settings).isoformat(),
            ),
            source_health=source_health,
            latest_runs=latest_runs,
            health_labels={
                "source_active": "入口可访问",
                "source_degraded": "入口待修复",
                "source_blocked": "访问受限",
                "source_error": "检查异常",
                "unknown": "尚未检查",
            },
        )

    @app.get("/landscape")
    def landscape() -> str:
        return render_template(
            "landscape.html",
            landscape=load_employment_landscape(),
        )
    @app.get("/healthz")
    def healthz() -> Any:
        backup = DatabaseBackupManager(settings).latest_status(
            max_age_seconds=max(1, settings.backup_min_interval_minutes) * 60
        )
        persisted_worker = database.get_service_heartbeat("worker")
        # A copied review database can legitimately contain the last heartbeat
        # from a prior Worker process. Do not present that historical row as a
        # currently running collector when this runtime explicitly disables
        # automation. The old timestamp is retained as diagnostic context only.
        if not settings.worker_automation_enabled:
            worker = {
                "service_name": "worker",
                "status": "paused",
                "automation_enabled": False,
                "detail": "Worker automation is disabled by current configuration.",
                "last_persisted_heartbeat_at": (
                    persisted_worker or {}
                ).get("updated_at"),
            }
        elif persisted_worker is None:
            worker = {
                "service_name": "worker",
                "status": "unknown",
                "automation_enabled": True,
                "detail": "No worker heartbeat has been recorded.",
            }
        else:
            worker = {**persisted_worker, "automation_enabled": True}
        return jsonify(
            {
                "status": "ok",
                "release": settings.release_info(),
                "open_jobs": database.count_open_jobs(
                    as_of_date=local_today(settings).isoformat()
                ),
                "latest_report": (
                    database.latest_daily_report() or {}
                ).get("report_date"),
                "worker": worker,
                "backup": backup_health_payload(backup),
            }
        )

    @app.get("/readinessz")
    def readinessz() -> Any:
        """Expose the internal publication gate without changing liveness.

        ``/healthz`` is intentionally a process check used by Docker.  This
        endpoint is the deployment/readiness check: a running web process can
        return 503 while workers, source freshness, backups, or validation
        evidence are not safe for student-facing publication.
        """
        report = build_production_readiness(settings, database)
        ready = bool(report.get("internal_ready"))
        response = jsonify(
            {
                "status": "ready" if ready else "not_ready",
                "internal_ready": ready,
            }
        )
        response.status_code = 200 if ready else 503
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/version")
    def version() -> Any:
        """Expose release identity for deployment and rollback verification."""
        return jsonify(
            {
                "service": "cupb-geoscience-job-hub",
                **settings.release_info(),
            }
        )

    @app.get("/api/jobs")
    def jobs_api() -> Any:
        page = max(request.args.get("page", 1, type=int) or 1, 1)
        business_date = local_today(settings).isoformat()
        profile = requested_profile()
        scope = request.args.get("scope", "").strip()
        if scope not in {"", "official_pending", DISCOVERY_SCOPE}:
            scope = ""
        official_pending = scope == "official_pending"
        student_discovery = scope == DISCOVERY_SCOPE
        province = request.args.get("province", "").strip() or None
        if province and province not in PROVINCES:
            return jsonify({"error": "province is not a supported mainland province"}), 400
        match_filter = request.args.get("match", "").strip()
        if match_filter not in {"", "explicit", "review"}:
            match_filter = ""
        publication_layer = request.args.get("layer", "").strip() or None
        if publication_layer not in {"", *PUBLICATION_LAYERS - {"not_public"}}:
            publication_layer = None
        records, total = list_display_jobs(
            page=page,
            category=request.args.get("category", "").strip() or None,
            degree=(
                request.args.get("degree", "").strip() or None
                if profile is None
                else None
            ),
            province=province,
            relevance_band=request.args.get("relevance", "").strip() or None,
            query=request.args.get("q", "").strip() or None,
            profile=profile,
            match_filter=match_filter,
            publication_layer=publication_layer,
            scope=scope or "current",
        )
        discovery_counts: dict[str, int] | None = None
        if student_discovery:
            all_candidates, _ = database.list_jobs(
                page_size=None,
                only_open=False,
                student_visible=False,
                as_of_date=business_date,
            )
            _all_discovery, discovery_counts = build_student_discovery_jobs(
                all_candidates,
                as_of_date=business_date,
                artifact_candidates=database.list_artifact_job_candidates(
                    review_status="official_content_verified", limit=500
                ),
            )
        return jsonify(
            {
                "items": [_student_api_job(record) for record in records],
                "total": total,
                "page": page,
                "scope": scope or "current",
                "counts_as_current": not official_pending and not student_discovery,
                "as_of_date": business_date,
                "discovery": (
                    {
                        "scope": DISCOVERY_SCOPE,
                        "counts_as_current": False,
                        "note": "官方信息发现层；包含明确标注的待核字段，不等同全部可直接投递。",
                        "counts": discovery_counts or {},
                    }
                    if student_discovery
                    else None
                ),
                "profile": (
                    {"id": profile.id, "label": profile.label}
                    if profile
                    else None
                ),
            }
        )

    @app.get("/api/coverage")
    def coverage_api() -> Any:
        return jsonify(
            build_coverage_report(
                database,
                snapshot_date=local_today(settings).isoformat(),
            )
        )

    def require_admin(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            token = request.headers.get("X-Admin-Token", "")
            expected = settings.admin_token
            if not expected or not hmac.compare_digest(token, expected):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    def admin_csrf_token() -> str:
        """Return the per-session token required by browser-admin forms."""
        token = session.get("admin_csrf_token")
        if not isinstance(token, str) or not token:
            token = secrets.token_urlsafe(32)
            session["admin_csrf_token"] = token
        return token

    def _safe_admin_next(value: str | None) -> str:
        """Accept only local workbench destinations after a login."""
        candidate = str(value or "").strip()
        return candidate if candidate.startswith("/") and not candidate.startswith("//") else url_for("admin_dashboard")

    def require_admin_session(
        view: Callable[..., Any] | None = None,
        *,
        permission: str = "dashboard.view",
    ) -> Callable[..., Any]:
        """Protect HTML workbench routes with role-aware permissions."""

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(target)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                user = current_admin_user()
                if user is None:
                    session.clear()
                    return redirect(
                        url_for("admin_login", next=_safe_admin_next(request.full_path))
                    )
                if permission and not admin_can(permission):
                    abort(403)
                return target(*args, **kwargs)

            return wrapped

        return decorator(view) if view is not None else decorator

    def require_admin_form(
        view: Callable[..., Any] | None = None,
        *,
        permission: str = "dashboard.view",
    ) -> Callable[..., Any]:
        """Require an authenticated role and a matching form CSRF token."""

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(target)
            @require_admin_session(permission=permission)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                supplied = str(request.form.get("csrf_token") or "")
                if not hmac.compare_digest(supplied, admin_csrf_token()):
                    abort(400)
                return target(*args, **kwargs)

            return wrapped

        return decorator(view) if view is not None else decorator

    @app.after_request
    def prevent_private_admin_caching(response: Any) -> Any:
        """Do not allow browsers or intermediaries to retain private evidence."""
        if request.path == "/admin" or request.path.startswith(("/admin/", "/api/admin/")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    def _admin_sources() -> list[dict[str, Any]]:
        return sorted(database.list_sources(), key=lambda item: (str(item["name"]), str(item["id"])))

    def _lead_status_options(lead: dict[str, Any]) -> list[tuple[str, str]]:
        current = str(lead.get("verification_status") or "candidate")
        allowed = CANDIDATE_LEAD_TRANSITIONS.get(current, frozenset({current}))
        return [
            (status, ADMIN_LEAD_STATUS_LABELS.get(status, status))
            for status in sorted(allowed, key=lambda item: (item != current, item))
            if status != "published"
        ]

    def _lead_form_changes(lead: dict[str, Any]) -> dict[str, Any]:
        """Translate a reviewed HTML form into the existing lead contract.

        This keeps all non-editable batch/provenance keys intact.  The form
        changes only the evidence fields an operator can actually verify.
        """

        metadata = dict(lead.get("metadata") or {})
        for key in (
            "employer",
            "description",
            "summary",
            "location",
            "deadline_date",
            "match_text",
            "qualification_text",
        ):
            value = str(request.form.get(f"metadata_{key}") or "").strip()
            if value:
                metadata[key] = value
            else:
                metadata.pop(key, None)

        field_evidence = metadata.get("field_evidence")
        if not isinstance(field_evidence, dict):
            field_evidence = {}
        else:
            field_evidence = dict(field_evidence)
        for field_name, form_name in (
            ("专业要求", "evidence_major"),
            ("学历要求", "evidence_degree"),
            ("岗位代码", "evidence_position_code"),
        ):
            value = str(request.form.get(form_name) or "").strip()
            if value:
                field_evidence[field_name] = value
            else:
                field_evidence.pop(field_name, None)
        metadata["field_evidence"] = field_evidence

        changes: dict[str, Any] = {
            "employer_hint": str(request.form.get("employer_hint") or "").strip(),
            "location_hint": str(request.form.get("location_hint") or "").strip(),
            "province_hint": str(request.form.get("province_hint") or "").strip(),
            "published_date": str(request.form.get("published_date") or "").strip(),
            "official_url": str(request.form.get("official_url") or "").strip(),
            "official_table_url": str(request.form.get("official_table_url") or "").strip(),
            "official_application_url": str(
                request.form.get("official_application_url") or ""
            ).strip(),
            "official_source_id": str(request.form.get("official_source_id") or "").strip(),
            "verification_status": str(
                request.form.get("verification_status") or lead["verification_status"]
            ).strip(),
            "verification_note": str(request.form.get("verification_note") or "").strip(),
            "trust_grade": str(request.form.get("trust_grade") or "").strip(),
            "reviewed_by": str(request.form.get("reviewed_by") or "").strip(),
            "metadata": metadata,
        }
        # A registered source assessment is automatic. Only an explicit
        # human override is ever sent through the manual-approval pathway.
        if request.form.get("manual_domain_approved") == "yes":
            changes["official_domain_status"] = "manual_review_approved"
        return changes

    def _publish_candidate_lead(lead_id: int) -> tuple[dict[str, Any], int]:
        """Publish one verified lead through the same gate for UI and API."""
        lead = database.get_candidate_lead(lead_id)
        if lead is None:
            return {"error": "Candidate lead was not found."}, 404
        try:
            job_id, outcome, published = publish_verified_candidate_lead(
                database, pipeline, lead_id
            )
        except ValueError as error:
            return {"error": str(error)}, 409
        return {"job_id": job_id, "outcome": outcome, "lead": published}, 201

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login() -> Any:
        next_url = _safe_admin_next(request.values.get("next"))
        if request.method == "POST":
            supplied_csrf = str(request.form.get("csrf_token") or "")
            username = str(request.form.get("username") or "").strip()
            password = str(request.form.get("password") or "")
            user = database.get_admin_user_by_username(username)
            valid = bool(
                hmac.compare_digest(supplied_csrf, admin_csrf_token())
                and settings.admin_password_hash
                and user
                and bool(user.get("enabled"))
                and check_password_hash(str(user.get("password_hash") or ""), password)
            )
            if not valid:
                return render_template(
                    "admin_login.html",
                    csrf_token=admin_csrf_token(),
                    next_url=next_url,
                    error=(
                        "管理员账户尚未配置。"
                        if not settings.admin_password_hash
                        else "用户名或密码错误。"
                    ),
                ), 403
            session.clear()
            session.permanent = True
            session["admin_user_id"] = int(user["id"])
            session["admin_username"] = str(user["username"])
            session["admin_role"] = str(user["role"])
            session["admin_csrf_token"] = secrets.token_urlsafe(32)
            database.mark_admin_login(int(user["id"]))
            return redirect(next_url)
        if current_admin_user() is not None:
            return redirect(next_url)
        return render_template(
            "admin_login.html",
            csrf_token=admin_csrf_token(),
            next_url=next_url,
            error=None,
        )

    @app.post("/admin/logout")
    @require_admin_form
    def admin_logout() -> Any:
        session.clear()
        return redirect(url_for("admin_login"))

    @app.get("/admin")
    @require_admin_session
    def admin_dashboard() -> str:
        leads = database.list_candidate_leads(limit=5000)
        statuses = {
            status: sum(1 for lead in leads if lead["verification_status"] == status)
            for status in ADMIN_LEAD_STATUS_LABELS
        }
        registry = load_discovery_source_registry()
        jobs, _ = database.list_jobs(
            page_size=None, as_of_date=local_today(settings).isoformat()
        )
        return render_template(
            "admin_dashboard.html",
            csrf_token=admin_csrf_token(),
            lead_statuses=statuses,
            lead_status_labels=ADMIN_LEAD_STATUS_LABELS,
            analytics=database.analytics_summary(
                end_day=local_today(settings).isoformat(), days=30
            ),
            worker_heartbeat=database.get_service_heartbeat("worker"),
            worker_runs=database.recent_worker_runs(run_kind="daily_cycle", limit=8),
            recent_failures=database.recent_crawl_failures(limit=8),
            recent_events=database.recent_candidate_lead_events(limit=8),
            discovery=discovery_funnel(
                leads,
                registry,
                jobs=jobs,
                third_party_batches=database.list_third_party_batches(limit=1000),
            ),
            third_party_batches=database.list_third_party_batches(limit=20),
            artifact_statuses=database.list_source_artifact_status_counts(),
            three_oil_entry=three_oil_entry_evidence_summary(
                load_three_oil_entry_evidence()
            ),
            three_oil=build_three_oil_catalog_audit(
                organization_registry=load_organization_registry(),
                source_records=database.list_sources(),
                job_records=jobs,
                external_identity_evidence=build_phase4_external_identity_evidence(),
            ),
        )

    @app.get("/admin/leads")
    @require_admin_session(permission="leads.view")
    def admin_leads() -> str:
        selected_status = request.args.get("status", "").strip() or None
        selected_source = request.args.get("discovery_source_id", "").strip() or None
        selected_province = request.args.get("province", "").strip() or None
        return render_template(
            "admin_leads.html",
            csrf_token=admin_csrf_token(),
            leads=database.list_candidate_leads(
                selected_status,
                discovery_source_id=selected_source,
                province=selected_province,
                limit=5000,
            ),
            statuses=ADMIN_LEAD_STATUS_LABELS,
            discovery_sources=load_discovery_source_registry()["sources"],
            selected={
                "status": selected_status or "",
                "discovery_source_id": selected_source or "",
                "province": selected_province or "",
            },
        )

    @app.get("/admin/three-oil-entries")
    @require_admin_session
    def admin_three_oil_entries() -> str:
        """Render the read-only unit-by-unit entrance-evidence ledger.

        This page is intentionally a review surface, not a source-registration
        or crawler control panel. Updating an entry requires a separately
        reviewed data change with its official evidence retained on disk.
        """

        payload = load_three_oil_entry_evidence()
        selected_group = request.args.get("group_id", "").strip()
        selected_status = request.args.get("status", "").strip()
        rows = list(payload["units"])
        if selected_group:
            rows = [item for item in rows if item["group_id"] == selected_group]
        if selected_status:
            rows = [
                item
                for item in rows
                if item["unit_specific_entry_status"] == selected_status
            ]
        return render_template(
            "admin_three_oil_entries.html",
            csrf_token=admin_csrf_token(),
            entries=rows,
            summary=three_oil_entry_evidence_summary(payload),
            status_labels=ADMIN_THREE_OIL_ENTRY_STATUS_LABELS,
            selected={"group_id": selected_group, "status": selected_status},
            source_matrix=payload.get("source_matrix", {}),
        )

    @app.route("/admin/leads/<int:lead_id>", methods=["GET", "POST"])
    @require_admin_session(permission="leads.view")
    def admin_lead_detail(lead_id: int) -> Any:
        lead = database.get_candidate_lead(lead_id)
        if lead is None:
            abort(404)
        if request.method == "POST":
            if not admin_can("leads.edit"):
                abort(403)
            supplied_csrf = str(request.form.get("csrf_token") or "")
            if not hmac.compare_digest(supplied_csrf, admin_csrf_token()):
                abort(400)
            action = str(request.form.get("action") or "save")
            if action == "publish" and not admin_can("leads.publish"):
                abort(403)
            try:
                if action == "save":
                    database.update_candidate_lead(lead_id, _lead_form_changes(lead))
                    flash("审核记录已保存。", "success")
                elif action == "publish":
                    result, status_code = _publish_candidate_lead(lead_id)
                    if status_code != 201:
                        flash(str(result["error"]), "error")
                    else:
                        flash(f"已按官方证据创建岗位 #{result['job_id']}。", "success")
                else:
                    abort(400)
            except ValueError as error:
                flash(str(error), "error")
            return redirect(url_for("admin_lead_detail", lead_id=lead_id))
        return render_template(
            "admin_lead_detail.html",
            csrf_token=admin_csrf_token(),
            lead=lead,
            status_options=_lead_status_options(lead),
            domain_labels=ADMIN_DOMAIN_STATUS_LABELS,
            sources=_admin_sources(),
            events=database.list_candidate_lead_events(lead_id),
            mentions=database.list_candidate_lead_mentions(lead_id),
            can_edit=admin_can("leads.edit"),
            can_publish=admin_can("leads.publish"),
        )

    @app.get("/api/admin/source-funnel")
    @require_admin
    def source_funnel_api() -> Any:
        """Expose the private source-throughput ledger for expansion work."""
        sources = database.list_sources()
        jobs, _ = database.list_jobs(
            page_size=None,
            as_of_date=local_today(settings).isoformat(),
        )
        return jsonify(
            build_source_funnel(
                sources=sources,
                jobs=jobs,
                crawl_runs=database.list_crawl_runs(limit=10_000),
                health_by_id={
                    item["source_id"]: item for item in database.list_source_health()
                },
                source_tasks=database.list_source_tasks(),
                artifact_status_counts=database.list_source_artifact_status_counts(),
                target_plan=load_effective_job_target_plan(),
                capture_freshness_by_id={
                    str(source["id"]): capture
                    for source in sources
                    if (
                        capture := database.get_source_capture_freshness(
                            str(source["id"])
                        )
                    ) is not None
                },
            )
        )

    @app.get("/api/admin/growth-metrics")
    @require_admin
    def growth_metrics_api() -> Any:
        """Expose separated lead and first-party throughput metrics."""
        today = local_today(settings).isoformat()
        leads = database.list_candidate_leads(limit=5000)
        jobs, _ = database.list_jobs(page_size=None, as_of_date=today)
        discovery = discovery_funnel(
            leads,
            load_discovery_source_registry(),
            jobs=jobs,
            third_party_batches=database.list_third_party_batches(limit=1000),
        )
        sources = database.list_sources()
        source_report = build_source_funnel(
            sources=sources,
            jobs=jobs,
            crawl_runs=database.list_crawl_runs(limit=10_000),
            health_by_id={item["source_id"]: item for item in database.list_source_health()},
            source_tasks=database.list_source_tasks(),
            artifact_status_counts=database.list_source_artifact_status_counts(),
            target_plan=load_effective_job_target_plan(),
            capture_freshness_by_id={
                str(source["id"]): capture
                for source in sources
                if (capture := database.get_source_capture_freshness(str(source["id"])))
                is not None
            },
        )
        return jsonify(
            {
                "as_of_date": today,
                "third_party_leads": discovery["growth_metrics"],
                "third_party_by_discovery_source": discovery["source_growth_metrics"],
                "official_source_summary": source_report["summary"],
                "official_sources": [
                    {
                        "source_id": row["source_id"],
                        "growth_metrics": row["growth_metrics"],
                        "stage": row["stage"],
                        "published_domestic_jobs": row["published_domestic_jobs"],
                    }
                    for row in source_report["rows"]
                ],
            }
        )

    @app.get("/api/admin/analytics")
    @require_admin
    def analytics_api() -> Any:
        days = request.args.get("days", 30, type=int)
        return jsonify(
            database.analytics_summary(
                end_day=local_today(settings).isoformat(), days=days
            )
        )

    @app.get("/api/admin/source-fallback-audit")
    @require_admin
    def source_fallback_audit_api() -> Any:
        """Expose CNPC/Sinopec fallback entries without treating candidates as sources."""
        return jsonify(
            build_fallback_audit(
                load_fallback_plan(),
                source_records=database.list_sources(),
            )
        )

    @app.get("/api/admin/organization-matrix")
    @require_admin
    def organization_matrix_api() -> Any:
        """Show private source-readiness detail for the registered organizations."""
        organization_role = request.args.get("organization_role", "").strip() or None
        affiliation = request.args.get("affiliation", "").strip() or None
        if organization_role and organization_role not in ORGANIZATION_ROLES:
            return jsonify({"error": "Unsupported organization_role."}), 400

        registry = load_organization_registry()
        sources = database.list_sources()
        source_health = {
            item["source_id"]: item for item in database.list_source_health()
        }
        latest_runs = {
            item["source_id"]: item for item in database.list_latest_crawl_runs()
        }
        rows = organization_matrix_rows(
            registry,
            source_records=sources,
            organization_role=organization_role,
            affiliation=affiliation,
        )
        for row in rows:
            for channel in row["channels"]:
                source_id = channel.get("source_id")
                if not source_id:
                    channel["source_health_status"] = None
                    channel["latest_crawl_status"] = None
                    continue
                health = source_health.get(str(source_id), {})
                latest_run = latest_runs.get(str(source_id), {})
                channel["source_health_status"] = health.get("status")
                channel["latest_crawl_status"] = latest_run.get("status")
                channel["last_synced_at"] = latest_run.get("finished_at")

        return jsonify(
            {
                "summary": organization_matrix_summary(
                    registry,
                    source_records=sources,
                ),
                "filters": {
                    "organization_role": organization_role,
                    "affiliation": affiliation,
                },
                "items": rows,
            }
        )

    @app.get("/api/admin/three-oil-catalog-audit")
    @require_admin
    def three_oil_catalog_audit_api() -> Any:
        """Expose current three-oil unit and job-row binding evidence privately."""
        jobs, _ = database.list_jobs(
            page_size=None,
            as_of_date=local_today(settings).isoformat(),
        )
        return jsonify(
            build_three_oil_catalog_audit(
                organization_registry=load_organization_registry(),
                source_records=database.list_sources(),
                job_records=jobs,
                external_identity_evidence=build_phase4_external_identity_evidence(),
            )
        )

    @app.get("/api/admin/three-oil-entry-evidence")
    @require_admin
    def three_oil_entry_evidence_api() -> Any:
        """Expose the distinct unit-entry evidence ledger to administrators."""
        payload = load_three_oil_entry_evidence()
        return jsonify(
            {
                "summary": three_oil_entry_evidence_summary(payload),
                "items": payload["units"],
                "source_matrix": payload.get("source_matrix", {}),
            }
        )

    @app.get("/api/admin/national-source-matrix")
    @require_admin
    def national_source_matrix_api() -> Any:
        """Show national-energy acquisition decisions to administrators only."""
        affiliation = request.args.get("affiliation", "").strip() or None
        organization_role = request.args.get("organization_role", "").strip() or None
        runtime_status = request.args.get("runtime_status", "").strip() or None
        if organization_role and organization_role not in ORGANIZATION_ROLES:
            return jsonify({"error": "Unsupported organization_role."}), 400
        if runtime_status and runtime_status not in NATIONAL_RUNTIME_STATUSES:
            return jsonify({"error": "Unsupported runtime_status."}), 400

        matrix = load_national_source_matrix()
        registry = load_organization_registry()
        sources = database.list_sources()
        source_health = database.list_source_health()
        latest_runs = database.list_latest_crawl_runs()
        rows = national_source_matrix_rows(
            matrix,
            organization_registry=registry,
            source_records=sources,
            source_health=source_health,
            latest_runs=latest_runs,
            affiliation=affiliation,
            organization_role=organization_role,
            runtime_status=runtime_status,
        )
        return jsonify(
            {
                "summary": national_source_matrix_summary(
                    matrix,
                    organization_registry=registry,
                    source_records=sources,
                    source_health=source_health,
                    latest_runs=latest_runs,
                ),
                "filters": {
                    "affiliation": affiliation,
                    "organization_role": organization_role,
                    "runtime_status": runtime_status,
                },
                "items": rows,
            }
        )

    @app.get("/api/admin/national-source-probes")
    @require_admin
    def national_source_probes_api() -> Any:
        """Show read-only endpoint probe evidence to administrators only."""
        probes = load_national_source_probes()
        return jsonify(
            {
                "summary": national_source_probe_summary(probes),
                "items": probes["probes"],
            }
        )

    @app.get("/api/admin/sinopec-capture")
    @require_admin
    def sinopec_capture_api() -> Any:
        """Expose the private Sinopec SPA capture status to administrators."""
        source = database.get_source("sinopec-career")
        if source is None:
            return jsonify({"error": "sinopec-career source is not registered"}), 404
        config = source.get("config", {})
        snapshot_path = PROJECT_ROOT / str(config.get("snapshot_path") or "")
        try:
            capture = load_sinopec_capture(
                snapshot_path,
                allowed_hosts=list(config.get("allowed_hosts", [])),
                require_complete_manifest=False,
            )
        except (OSError, ValueError) as error:
            return jsonify({"error": str(error)}), 503
        return jsonify(
            {
                "source_id": "sinopec-career",
                "enabled": bool(source.get("enabled")),
                "summary": sinopec_capture_summary(capture),
                "platform_url": capture["platform_url"],
                "captured_at": capture["captured_at"],
                "enterprises": capture["enterprises"],
            }
        )

    @app.get("/api/admin/cnpc-matrix")
    @require_admin
    def cnpc_matrix_api() -> Any:
        """Expose CNPC unit registration and snapshot binding to administrators."""
        source = database.get_source("cnpc-career")
        snapshot_path = PROJECT_ROOT / str(
            (source or {}).get("config", {}).get("snapshot_path")
            or "data/verified/cnpc-geoscience-20260924.json"
        )
        try:
            report = build_cnpc_matrix_report(snapshot_path=snapshot_path)
        except CnpcMatrixError as error:
            return jsonify({"error": str(error), "source_id": "cnpc-career"}), 503
        return jsonify({"source_id": "cnpc-career", **report})

    @app.get("/api/admin/cnpc-browser-capture")
    @require_admin
    def cnpc_browser_capture_api() -> Any:
        """Expose the private CNPC browser index/detail audit."""
        source = database.get_source("cnpc-career")
        configured_path = str(
            (source or {}).get("config", {}).get("browser_index_capture_path")
            or "data/verified/cnpc-browser-index-20260925.json"
        )
        path = PROJECT_ROOT / configured_path
        try:
            report = build_cnpc_browser_capture_report(path)
        except CnpcBrowserCaptureError as error:
            return jsonify({"error": str(error), "capture_path": str(path)}), 503
        return jsonify({"capture_path": str(path), **report})

    @app.get("/api/admin/national-entry-probes")
    @require_admin
    def national_entry_probes_api() -> Any:
        """Show official entry targets and the latest private probe run."""
        targets = load_national_entry_targets()
        run = None
        run_error = None
        try:
            run = load_national_entry_probe_run()
        except ValueError as error:
            run_error = str(error)
        return jsonify(
            {
                "targets": targets["systems"],
                "run": run,
                "summary": national_entry_probe_summary(run) if run else None,
                "run_error": run_error,
            }
        )

    @app.get("/api/admin/source-validation-matrix")
    @require_admin
    def source_validation_matrix_api() -> Any:
        """Show private evidence and runtime context for provincial sources."""
        province = request.args.get("province", "").strip() or None
        role = request.args.get("role", "").strip() or None
        validation_stage = request.args.get("validation_stage", "").strip() or None
        if province and province not in PROVINCES:
            return jsonify({"error": "Unsupported province."}), 400
        if role and role not in REQUIRED_ROLES:
            return jsonify({"error": "Unsupported role."}), 400
        if validation_stage and validation_stage not in SOURCE_VALIDATION_STAGES:
            return jsonify({"error": "Unsupported validation_stage."}), 400

        sources = database.list_sources()
        source_health = {
            item["source_id"]: item for item in database.list_source_health()
        }
        latest_runs = {
            item["source_id"]: item for item in database.list_latest_crawl_runs()
        }
        registry = load_source_validation_registry()
        rows = source_validation_matrix_rows(
            registry,
            source_records=sources,
            province=province,
            role=role,
            validation_stage=validation_stage,
        )
        for row in rows:
            source_id = str(row["source_id"])
            row["source_health_status"] = source_health.get(source_id, {}).get(
                "status"
            )
            row["latest_crawl_status"] = latest_runs.get(source_id, {}).get(
                "status"
            )
            row["last_synced_at"] = latest_runs.get(source_id, {}).get(
                "finished_at"
            )
        return jsonify(
            {
                "summary": source_validation_summary(
                    registry,
                    source_records=sources,
                ),
                "filters": {
                    "province": province,
                    "role": role,
                    "validation_stage": validation_stage,
                },
                "items": rows,
            }
        )

    @app.get("/api/admin/source-tasks")
    @require_admin
    def source_tasks_api() -> Any:
        """Expose durable source queue state to the administrator only."""
        due_only = request.args.get("due_only", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        tasks = database.list_source_tasks(due_only=due_only)
        by_status: dict[str, int] = {}
        for task in tasks:
            status = str(task.get("status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
        return jsonify(
            {
                "summary": {
                    "count": len(tasks),
                    "by_status": by_status,
                    "due_only": due_only,
                },
                "items": tasks,
            }
        )

    @app.get("/api/admin/government-position-tasks")
    @require_admin
    def government_position_tasks_api() -> Any:
        """Expose official position-table activation tasks to administrators."""
        path = settings.government_position_registry_path
        if path is None:
            return jsonify({"items": [], "error": "position registry is not configured"}), 503
        try:
            registry = load_position_registry(path)
        except (OSError, ValueError) as error:
            return jsonify({"items": [], "error": str(error)}), 503
        now = datetime.now(ZoneInfo(settings.timezone))
        items = source_activation_tasks(
            registry,
            today=now.date().isoformat(),
            max_age_hours=settings.government_position_max_age_hours,
            source_verifications={
                str(item["source_id"]): item
                for item in database.list_government_source_verifications()
            },
            manual_confirmation_source_ids={
                str(source["id"])
                for source in database.list_sources()
                if requires_manual_government_evidence_confirmation(source)
            },
            now=now,
        )
        return jsonify(
            {
                "as_of": registry.get("as_of"),
                "checked_at": now.isoformat(),
                "items": items,
                "summary": {
                    "total": len(items),
                    "scheduled": sum(item["status"] == "scheduled" for item in items),
                    "due_revalidation": sum(item["status"] == "due_revalidation" for item in items),
                    "verified": sum(item["status"] == "verified" for item in items),
                    "expired": sum(item["status"] == "expired" for item in items),
                },
            }
        )

    @app.post("/api/admin/jobs")
    @require_admin
    def import_verified_job() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        required = ("title", "employer", "source_url")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400
        source_url = str(payload["source_url"]).strip()
        if not is_http_url(source_url):
            return jsonify({"error": "source_url must be an HTTP(S) URL."}), 400
        official_evidence_url = str(payload.get("official_evidence_url", "")).strip()
        if official_evidence_url and not is_http_url(official_evidence_url):
            return jsonify(
                {"error": "official_evidence_url must be an HTTP(S) URL."}
            ), 400
        source = database.get_source(
            str(payload.get("source_id", "official-manual-import"))
        )
        if source is None:
            return jsonify({"error": "Unknown source_id."}), 400
        field_evidence = payload.get("field_evidence")
        if not isinstance(field_evidence, dict):
            field_evidence = {}
        else:
            field_evidence = dict(field_evidence)
        if field_evidence:
            field_evidence.setdefault(
                "evidence_scope", "admin_verified_official_record"
            )
            field_evidence.setdefault("岗位", str(payload["title"]).strip())
        raw = RawPosting(
            title=str(payload["title"]).strip(),
            employer=str(payload["employer"]).strip(),
            source_url=source_url,
            application_url=str(payload.get("application_url", "")).strip() or None,
            text=str(payload.get("description", payload["title"])).strip(),
            summary=str(payload.get("summary", "")).strip(),
            published_date=str(payload.get("published_date", "")).strip() or None,
            deadline_date=str(payload.get("deadline_date", "")).strip() or None,
            location=str(payload.get("location", "")).strip() or None,
            external_id=str(payload.get("external_id", "")).strip() or None,
            official_evidence_url=official_evidence_url or None,
            match_text=str(payload.get("match_text", "")).strip() or None,
            qualification_text=str(payload.get("qualification_text", "")).strip() or None,
            field_evidence=field_evidence or None,
        )
        normalized = pipeline.normalize_posting(raw, source)
        job_id, outcome = database.save_job(normalized)
        return jsonify({"id": job_id, "outcome": outcome}), 201

    @app.route("/api/admin/leads", methods=["GET", "POST"])
    @require_admin
    def candidate_leads_api() -> Any:
        """Keep third-party discovery clues private until official verification."""
        if request.method == "GET":
            status = request.args.get("status", "").strip() or None
            discovery_source_id = request.args.get("discovery_source_id", "").strip() or None
            province = request.args.get("province", "").strip() or None
            return jsonify(
                {
                    "items": database.list_candidate_leads(
                        status,
                        discovery_source_id=discovery_source_id,
                        province=province,
                    )
                }
            )
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        for field in ("lead_url", "official_url", "official_application_url"):
            value = str(payload.get(field, "")).strip()
            if value and not is_http_url(value):
                return jsonify({"error": f"{field} must be an HTTP(S) URL."}), 400
        discovery_source_id = str(payload.get("discovery_source_id", "")).strip()
        if discovery_source_id:
            registry = load_discovery_source_registry()
            known = {str(item["id"]) for item in registry["sources"]}
            if discovery_source_id not in known:
                return jsonify({"error": "Unknown discovery_source_id."}), 400
        try:
            lead = database.create_candidate_lead(payload)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(lead), 201

    @app.get("/api/admin/third-party-batches")
    @require_admin
    def third_party_batches_api() -> Any:
        """Expose retained B-level table captures only to token-authenticated admins."""
        source_id = request.args.get("source_id", "").strip() or None
        return jsonify(
            {
                "items": database.list_third_party_batches(source_id=source_id, limit=1000),
            }
        )

    @app.get("/api/admin/discovery-sources")
    @require_admin
    def discovery_sources_api() -> Any:
        """Expose the private discovery-channel registry to administrators."""
        registry = load_discovery_source_registry()
        leads = database.list_candidate_leads(limit=5000)
        return jsonify(
            {
                "summary": discovery_source_summary(registry, lead_rows=leads),
                "items": discovery_source_rows(registry),
            }
        )

    @app.get("/api/admin/discovery-funnel")
    @require_admin
    def discovery_funnel_api() -> Any:
        """Show private lead conversion counts without exposing third-party content."""
        registry = load_discovery_source_registry()
        leads = database.list_candidate_leads(limit=5000)
        jobs, _ = database.list_jobs(
            page_size=None,
            as_of_date=local_today(settings).isoformat(),
        )
        return jsonify(
            discovery_funnel(
                leads,
                registry,
                jobs=jobs,
                third_party_batches=database.list_third_party_batches(limit=1000),
            )
        )

    @app.patch("/api/admin/leads/<int:lead_id>")
    @require_admin
    def update_candidate_lead_api(lead_id: int) -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        official_url = str(payload.get("official_url", "")).strip()
        if official_url and not is_http_url(official_url):
            return jsonify({"error": "official_url must be an HTTP(S) URL."}), 400
        official_application_url = str(
            payload.get("official_application_url", "")
        ).strip()
        if official_application_url and not is_http_url(official_application_url):
            return jsonify(
                {"error": "official_application_url must be an HTTP(S) URL."}
            ), 400
        try:
            lead = database.update_candidate_lead(lead_id, payload)
        except ValueError as error:
            return jsonify({"error": str(error)}), 409
        if lead is None:
            abort(404)
        return jsonify(lead)

    @app.post("/api/admin/leads/<int:lead_id>/publish")
    @require_admin
    def publish_candidate_lead_api(lead_id: int) -> Any:
        payload, status_code = _publish_candidate_lead(lead_id)
        return jsonify(payload), status_code

    @app.route("/api/admin/artifacts", methods=["GET", "POST"])
    @require_admin
    def source_artifacts_api() -> Any:
        """Maintain private metadata for official announcement attachments."""
        if request.method == "GET":
            source_id = request.args.get("source_id", "").strip() or None
            return jsonify({"items": database.list_source_artifacts(source_id)})
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        try:
            artifact = database.upsert_source_artifact(payload)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(artifact), 201

    @app.post("/api/admin/artifacts/<int:artifact_id>/process")
    @require_admin
    def process_source_artifact_api(artifact_id: int) -> Any:
        """Download and parse one official attachment into the private review queue."""
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        processor = app.extensions["attachment_processor"]
        try:
            result = processor.process(
                artifact_id,
                force_download=bool(payload.get("force_download", False)),
                extract=bool(payload.get("extract", True)),
            )
        except (AttachmentProcessingError, ValueError) as error:
            return jsonify({"error": str(error)}), 409
        return jsonify(result.as_dict()), 200

    @app.post("/api/admin/artifacts/discover")
    @require_admin
    def discover_source_artifacts_api() -> Any:
        """Find public PDF/Excel links on one approved announcement page."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        source_id = str(payload.get("source_id") or "").strip()
        parent_url = str(payload.get("parent_url") or "").strip()
        if not source_id or not parent_url:
            return jsonify({"error": "source_id and parent_url are required."}), 400
        processor = app.extensions["attachment_processor"]
        try:
            artifacts = processor.discover_from_page(
                source_id,
                parent_url,
                html=(str(payload["html"]) if "html" in payload else None),
            )
        except (AttachmentProcessingError, ValueError) as error:
            return jsonify({"error": str(error)}), 409
        return jsonify({"items": artifacts, "count": len(artifacts)}), 201

    @app.get("/api/admin/artifacts/<int:artifact_id>/rows")
    @require_admin
    def source_artifact_rows_api(artifact_id: int) -> Any:
        if database.get_source_artifact(artifact_id) is None:
            abort(404)
        limit = request.args.get("limit", 100, type=int)
        return jsonify({"items": database.list_source_artifact_rows(artifact_id, limit=limit)})

    @app.route("/api/admin/artifact-candidates", methods=["GET", "POST"])
    @require_admin
    def artifact_candidates_api() -> Any:
        if request.method == "GET":
            artifact_id = request.args.get("artifact_id", type=int)
            status = request.args.get("status", "").strip() or None
            return jsonify(
                {
                    "items": database.list_artifact_job_candidates(
                        artifact_id=artifact_id,
                        review_status=status,
                    )
                }
            )
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        try:
            candidate = database.upsert_artifact_job_candidate(payload)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(candidate), 201

    @app.patch("/api/admin/artifact-candidates/<int:candidate_id>")
    @require_admin
    def update_artifact_candidate_api(candidate_id: int) -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        try:
            candidate = database.update_artifact_job_candidate(candidate_id, payload)
        except ValueError as error:
            return jsonify({"error": str(error)}), 409
        if candidate is None:
            abort(404)
        return jsonify(candidate)

    @app.post("/api/admin/artifact-candidates/<int:candidate_id>/publish")
    @require_admin
    def publish_artifact_candidate_api(candidate_id: int) -> Any:
        candidate = database.get_artifact_job_candidate(candidate_id)
        if candidate is None:
            abort(404)
        if candidate["review_status"] != "official_content_verified":
            return jsonify({"error": "Only officially verified attachment candidates can be published."}), 409
        if candidate.get("extraction_confidence") == "low":
            return jsonify({"error": "Low-confidence OCR candidates require manual import after independent verification."}), 409
        source = database.get_source(str(candidate["source_id"]))
        if source is None:
            return jsonify({"error": "Candidate source is no longer registered."}), 409
        existing_evidence = candidate.get("field_evidence") or {}
        if not isinstance(existing_evidence, dict):
            existing_evidence = {}
        nested_fields = existing_evidence.get("fields")
        if not isinstance(nested_fields, dict):
            nested_fields = {}
        field_evidence = build_attachment_field_evidence(
            title=str(candidate["title"]),
            major=str(existing_evidence.get("专业范围") or nested_fields.get("major") or "") or None,
            degree=str(existing_evidence.get("学历要求") or nested_fields.get("degree") or "") or None,
            location=str(candidate.get("location") or "").strip() or None,
            artifact=candidate,
            row={
                "sheet_name": candidate.get("row_sheet_name"),
                "row_number": candidate.get("row_number"),
            },
            row_text=str(candidate.get("row_text") or ""),
            extra_evidence=existing_evidence,
        )
        location_basis = str(
            (candidate.get("field_evidence") or {}).get("工作地点依据") or ""
        ).strip()
        if location_basis:
            field_evidence["工作地点依据"] = location_basis
        raw = RawPosting(
            title=str(candidate["title"]),
            employer=str(candidate["employer"]),
            source_url=str(candidate["official_page_url"]),
            application_url=candidate.get("application_url"),
            text=str(candidate.get("description") or candidate["title"]),
            summary=str(candidate.get("summary") or ""),
            published_date=candidate.get("published_date"),
            deadline_date=candidate.get("deadline_date"),
            location=candidate.get("location"),
            external_id=f"artifact-candidate-{candidate_id}",
            official_evidence_url=str(candidate["official_page_url"]),
            field_evidence=field_evidence,
        )
        try:
            job_id, outcome, published = database.publish_artifact_job_candidate(
                candidate_id,
                pipeline.normalize_posting(raw, source),
                {
                    "artifact_id": candidate["artifact_id"],
                    "evidence_type": "attachment",
                    "field_name": "job_record",
                    "evidence_url": str(candidate["artifact_url"]),
                    "locator": f"{candidate['row_sheet_name']}!{candidate['row_number']}",
                    "excerpt": str(candidate.get("row_text") or "")[:2000],
                    "verification_status": "verified",
                    "metadata": {
                        "candidate_id": candidate_id,
                        "content_sha256": candidate.get("artifact_content_sha256"),
                        "parser_version": candidate.get("artifact_parser_version"),
                    },
                },
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 409
        return jsonify({"job_id": job_id, "outcome": outcome, "candidate": published}), 201

    @app.route("/api/admin/jobs/<int:job_id>/evidence", methods=["GET", "POST"])
    @require_admin
    def job_evidence_api(job_id: int) -> Any:
        """Expose per-job evidence only to the administrator, never publicly."""
        if database.find_job(job_id) is None:
            abort(404)
        if request.method == "GET":
            return jsonify({"items": database.list_job_evidence(job_id)})
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        try:
            evidence = database.add_job_evidence(job_id, payload)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(evidence), 201

    @app.get("/api/admin/leads/<int:lead_id>/events")
    @require_admin
    def candidate_lead_events_api(lead_id: int) -> Any:
        """Return the private verification history for one discovery lead."""
        if database.get_candidate_lead(lead_id) is None:
            abort(404)
        return jsonify({"items": database.list_candidate_lead_events(lead_id)})

    @app.get("/api/admin/leads/<int:lead_id>/mentions")
    @require_admin
    def candidate_lead_mentions_api(lead_id: int) -> Any:
        """Return every private discovery source that mentioned one lead."""
        if database.get_candidate_lead(lead_id) is None:
            abort(404)
        return jsonify({"items": database.list_candidate_lead_mentions(lead_id)})

    @app.post("/api/admin/publish")
    @require_admin
    def publish_report_api() -> Any:
        audit = audit_database(database, settings)
        if not audit["ok"]:
            return jsonify(
                {
                    "error": "日报发布前的数据审计未通过。",
                    "audit": audit,
                }
            ), 409
        report = publish_daily_report(database, settings)
        return jsonify(
            {
                "report_date": report["report_date"],
                "url": url_for("daily_report", report_date=report["report_date"]),
            }
        )

    @app.errorhandler(404)
    def not_found(_: Any) -> tuple[str, int]:
        return render_template("error.html", code=404, title="页面未找到"), 404

    @app.errorhandler(403)
    def forbidden(_: Any) -> tuple[str, int]:
        return render_template("error.html", code=403, title="无权访问此页面"), 403

    return app
