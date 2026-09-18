from __future__ import annotations

import hmac
from datetime import date, datetime, timezone
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, render_template, request, url_for

from job_hub.audit import audit_database
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.matching import CATEGORY_DESCRIPTIONS, CATEGORY_ORDER, DEGREE_ORDER
from job_hub.pipeline import JobPipeline
from job_hub.profiles import (
    StudentProfile,
    annotate_profile_match,
    get_student_profile,
    list_student_profiles,
)
from job_hub.reports import build_daily_report, local_today, publish_daily_report
from job_hub.sources import RawPosting


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

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "site_name": settings.site_name,
            "category_order": CATEGORY_ORDER,
            "category_descriptions": CATEGORY_DESCRIPTIONS,
            "degree_order": DEGREE_ORDER,
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

    def active_report() -> tuple[dict[str, Any], bool]:
        report = database.latest_daily_report()
        if report is not None:
            return report, False
        return build_daily_report(database, settings), True

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
        relevance_band: str | None,
        query: str | None,
        profile: StudentProfile | None,
        match_filter: str,
    ) -> tuple[list[dict[str, Any]], int]:
        if profile is None:
            return database.list_jobs(
                category=category,
                degree=degree,
                relevance_band=relevance_band,
                q=query,
                page=page,
            )

        # A profile match requires the raw announcement as well as derived tags.
        # The source registry bounds the public corpus, and no student data is kept.
        candidates, _ = database.list_jobs(
            category=category,
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
            report=report,
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
            report=report,
            is_preview=is_preview,
            report_dates=database.list_report_dates(30),
        )

    @app.get("/jobs")
    def jobs() -> str:
        page = max(request.args.get("page", 1, type=int), 1)
        category = request.args.get("category", "").strip() or None
        degree = request.args.get("degree", "").strip() or None
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
        job = database.find_job(job_id)
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
        return render_template(
            "about.html",
            sources=database.list_sources(),
            failures=database.recent_crawl_failures(),
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

    def require_admin(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            token = request.headers.get("X-Admin-Token", "")
            expected = settings.admin_token
            if not expected or not hmac.compare_digest(token, expected):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

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
        if urlparse(source_url).scheme not in {"http", "https"}:
            return jsonify({"error": "source_url must be an HTTP(S) URL."}), 400
        source = database.get_source(
            str(payload.get("source_id", "official-manual-import"))
        )
        if source is None:
            return jsonify({"error": "Unknown source_id."}), 400
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
        )
        normalized = pipeline.normalize_posting(raw, source)
        job_id, outcome = database.save_job(normalized)
        return jsonify({"id": job_id, "outcome": outcome}), 201

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
