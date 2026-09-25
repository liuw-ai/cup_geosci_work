from __future__ import annotations

import hmac
from datetime import date, datetime, timezone
from functools import wraps
from typing import Any, Callable
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, render_template, request, url_for

from job_hub.audit import audit_database
from job_hub.attachments import (
    AttachmentProcessingError,
    OfficialAttachmentProcessor,
    build_attachment_field_evidence,
)
from job_hub.config import PROJECT_ROOT, Settings
from job_hub.contracts import (
    NATIONAL_RUNTIME_STATUSES,
    ORGANIZATION_ROLES,
    SOURCE_VALIDATION_STAGES,
    is_http_url,
)
from job_hub.coverage import build_coverage_report
from job_hub.cnpc_matrix import CnpcMatrixError, build_cnpc_matrix_report
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
from job_hub.matching import CATEGORY_DESCRIPTIONS, CATEGORY_ORDER, DEGREE_ORDER
from job_hub.organizations import (
    load_organization_registry,
    organization_matrix_rows,
    organization_matrix_summary,
)
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
from job_hub.profiles import (
    StudentProfile,
    annotate_profile_match,
    get_student_profile,
    list_student_profiles,
)
from job_hub.source_targets import REQUIRED_ROLES
from job_hub.source_validation import (
    load_source_validation_registry,
    source_validation_matrix_rows,
    source_validation_summary,
)
from job_hub.reports import build_daily_report, local_today, publish_daily_report
from job_hub.sources import RawPosting
from job_hub.sinopec import load_sinopec_capture, sinopec_capture_summary


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    settings.ensure_runtime_paths()
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.json.ensure_ascii = False
    app.extensions["settings"] = settings
    app.extensions["database"] = database
    app.extensions["pipeline"] = pipeline
    app.extensions["attachment_processor"] = OfficialAttachmentProcessor(settings, database)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "site_name": settings.site_name,
            "category_order": CATEGORY_ORDER,
            "category_descriptions": CATEGORY_DESCRIPTIONS,
            "category_display_names": CATEGORY_DISPLAY_NAMES,
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
        for key in ("new_jobs", "updated_jobs", "deadline_jobs"):
            displayed[key] = [
                job
                for item in report.get(key, [])
                if (job := database.find_public_job(int(item["id"]))) is not None
            ]
        stats = dict(report.get("stats", {}))
        stats.update(
            {
                "new": len(displayed["new_jobs"]),
                "updated": len(displayed["updated_jobs"]),
                "deadline_soon": len(displayed["deadline_jobs"]),
                "open_total": database.count_open_jobs(),
            }
        )
        displayed["stats"] = stats
        return displayed

    def active_report() -> tuple[dict[str, Any], bool]:
        report = database.latest_daily_report()
        # A frozen report is immutable, but the homepage must not keep showing
        # yesterday's date when today's 20:00 publication has not happened yet.
        # Build an explicit preview for the local calendar day and keep the
        # older snapshot available through /daily/latest and its dated URL.
        if report is not None and str(report.get("report_date")) == local_today(settings).isoformat():
            return report_for_display(report), False
        return report_for_display(
            build_daily_report(database, settings, local_today(settings))
        ), True

    def requested_profile() -> StudentProfile | None:
        profile_id = request.args.get("profile", "").strip()
        profile = get_student_profile(profile_id)
        if profile_id and profile is None:
            abort(404)
        return profile

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
    ) -> tuple[list[dict[str, Any]], int]:
        if profile is None:
            return database.list_jobs(
                category=category,
                degree=degree,
                province=province,
                relevance_band=relevance_band,
                q=query,
                page=page,
            )

        # A profile match requires the raw announcement as well as derived tags.
        # The source registry bounds the public corpus, and no student data is kept.
        candidates, _ = database.list_jobs(
            category=category,
            province=province,
            relevance_band=relevance_band,
            q=query,
            page_size=None,
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

    @app.get("/")
    def home() -> str:
        report, is_preview = active_report()
        featured = sorted(
            report["new_jobs"] + report["updated_jobs"],
            key=lambda item: (-item["relevance_score"], item["deadline_date"] or "9999-12-31"),
        )[:6]
        if not featured:
            featured, _ = database.list_jobs(page_size=6)
        return render_template(
            "home.html",
            report=report,
            is_preview=is_preview,
            featured=featured,
            categories=database.list_categories(),
            report_dates=database.list_report_dates(14),
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
        page = max(request.args.get("page", 1, type=int), 1)
        category = request.args.get("category", "").strip() or None
        degree = request.args.get("degree", "").strip() or None
        province = request.args.get("province", "").strip() or None
        if province and province not in PROVINCES:
            abort(404)
        relevance_band = request.args.get("relevance", "").strip() or None
        query = request.args.get("q", "").strip() or None
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
            },
            categories=database.list_categories(),
            profile=profile,
        )

    @app.get("/jobs/<int:job_id>")
    def job_detail(job_id: int) -> str:
        job = database.find_public_job(job_id)
        if job is None:
            abort(404)
        profile = requested_profile()
        return render_template(
            "job_detail.html",
            job=annotate_profile_match(job, profile),
            profile=profile,
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
        return jsonify(
            {
                "status": "ok",
                "open_jobs": database.count_open_jobs(),
                "latest_report": (
                    database.latest_daily_report() or {}
                ).get("report_date"),
                "worker": database.get_service_heartbeat("worker"),
            }
        )

    @app.get("/api/jobs")
    def jobs_api() -> Any:
        page = max(request.args.get("page", 1, type=int), 1)
        profile = requested_profile()
        province = request.args.get("province", "").strip() or None
        if province and province not in PROVINCES:
            return jsonify({"error": "province is not a supported mainland province"}), 400
        match_filter = request.args.get("match", "").strip()
        if match_filter not in {"", "explicit", "review"}:
            match_filter = ""
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
        )
        return jsonify(
            {
                "items": records,
                "total": total,
                "page": page,
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
        for field in ("lead_url", "official_url"):
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
        return jsonify(discovery_funnel(leads, registry))

    @app.patch("/api/admin/leads/<int:lead_id>")
    @require_admin
    def update_candidate_lead_api(lead_id: int) -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        official_url = str(payload.get("official_url", "")).strip()
        if official_url and not is_http_url(official_url):
            return jsonify({"error": "official_url must be an HTTP(S) URL."}), 400
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
        lead = database.get_candidate_lead(lead_id)
        if lead is None:
            abort(404)
        if lead["verification_status"] != "official_content_verified":
            return jsonify(
                {"error": "Only official-content-verified leads can be published."}
            ), 409
        if not is_http_url(str(lead.get("official_url") or "")):
            return jsonify({"error": "Lead has no valid official_url."}), 409
        if lead.get("official_domain_status") not in {
            "registered_source_match",
            "manual_review_approved",
        }:
            return jsonify(
                {
                    "error": (
                        "Lead needs a registered-source match or explicit "
                        "manual-domain approval."
                    )
                }
            ), 409
        metadata = lead.get("metadata", {})
        employer = str(metadata.get("employer") or lead.get("employer_hint") or "").strip()
        if not employer:
            return jsonify({"error": "Verified lead needs an employer before publication."}), 409
        source = database.get_source(
            str(
                metadata.get("source_id")
                or lead.get("official_source_id")
                or "official-manual-import"
            )
        )
        if source is None:
            return jsonify({"error": "Configured source_id is not registered."}), 409
        field_evidence = metadata.get("field_evidence")
        if not isinstance(field_evidence, dict):
            field_evidence = {}
        else:
            field_evidence = dict(field_evidence)
        # This route is available only after an administrator verified the
        # official original. Record that provenance so manual and automated
        # sources pass through the same student-publication gate.
        if field_evidence:
            field_evidence.setdefault(
                "evidence_scope", "admin_verified_official_record"
            )
            field_evidence.setdefault(
                "岗位", str(metadata.get("title") or lead["title"]).strip()
            )
        raw = RawPosting(
            title=str(metadata.get("title") or lead["title"]).strip(),
            employer=employer,
            source_url=str(lead["official_url"]).strip(),
            application_url=str(metadata.get("application_url") or "").strip() or None,
            text=str(metadata.get("description") or lead["title"]).strip(),
            summary=str(metadata.get("summary") or "").strip(),
            published_date=str(
                metadata.get("published_date") or lead.get("published_date") or ""
            ).strip()
            or None,
            deadline_date=str(metadata.get("deadline_date") or "").strip() or None,
            location=str(metadata.get("location") or lead.get("location_hint") or "").strip()
            or None,
            external_id=f"verified-lead-{lead_id}",
            official_evidence_url=str(lead["official_url"]).strip(),
            match_text=str(metadata.get("match_text") or "").strip() or None,
            qualification_text=(
                str(metadata.get("qualification_text") or "").strip() or None
            ),
            field_evidence=field_evidence or None,
        )
        job_id, outcome = database.save_job(pipeline.normalize_posting(raw, source))
        try:
            published = database.mark_candidate_lead_published(lead_id, job_id)
        except ValueError as error:
            return jsonify({"error": str(error)}), 409
        return jsonify({"job_id": job_id, "outcome": outcome, "lead": published}), 201

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
        )
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
