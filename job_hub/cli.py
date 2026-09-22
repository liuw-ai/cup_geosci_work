from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from job_hub.audit import audit_database
from job_hub.attachments import (
    AttachmentProcessingError,
    OfficialAttachmentProcessor,
)
from job_hub.config import Settings
from job_hub.contracts import (
    NATIONAL_RUNTIME_STATUSES,
    ORGANIZATION_ROLES,
    SOURCE_VALIDATION_STAGES,
    is_http_url,
)
from job_hub.coverage import build_coverage_report
from job_hub.db import Database
from job_hub.discovery import (
    discovery_funnel,
    discovery_source_rows,
    discovery_source_summary,
    load_discovery_source_registry,
)
from job_hub.emailer import Mailer
from job_hub.locations import PROVINCES
from job_hub.pipeline import JobPipeline
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
from job_hub.reports import publish_daily_report
from job_hub.simulation import simulate_cohort
from job_hub.source_targets import REQUIRED_ROLES
from job_hub.source_validation import (
    load_source_validation_registry,
    source_validation_matrix_rows,
    source_validation_summary,
)
from job_hub.sources import RawPosting, SourceHealthProbe
from job_hub.worker import main as worker_main


def services() -> tuple[Settings, Database, JobPipeline]:
    settings = Settings.from_env()
    settings.ensure_runtime_paths()
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()
    return settings, database, pipeline


def import_verified_jobs(
    database: Database,
    pipeline: JobPipeline,
    path: Path,
) -> dict[str, int]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload if isinstance(payload, list) else [payload]
    results = {"created": 0, "updated": 0, "unchanged": 0}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each imported job must be a JSON object")
        for field in ("title", "employer", "source_url"):
            if not str(item.get(field, "")).strip():
                raise ValueError(f"Imported job is missing {field}")
        source_url = str(item["source_url"]).strip()
        if not is_http_url(source_url):
            raise ValueError("source_url must be an HTTP(S) URL")
        official_evidence_url = str(item.get("official_evidence_url", "")).strip()
        if official_evidence_url and not is_http_url(official_evidence_url):
            raise ValueError("official_evidence_url must be an HTTP(S) URL")
        source = database.get_source(
            str(item.get("source_id", "official-manual-import"))
        )
        if source is None:
            raise ValueError("source_id is not registered in data/sources.json")
        posting = RawPosting(
            title=str(item["title"]).strip(),
            employer=str(item["employer"]).strip(),
            source_url=source_url,
            application_url=str(item.get("application_url", "")).strip() or None,
            text=str(item.get("description", item["title"])).strip(),
            summary=str(item.get("summary", "")).strip(),
            published_date=str(item.get("published_date", "")).strip() or None,
            deadline_date=str(item.get("deadline_date", "")).strip() or None,
            location=str(item.get("location", "")).strip() or None,
            external_id=str(item.get("external_id", "")).strip() or None,
            official_evidence_url=official_evidence_url or None,
        )
        _, outcome = database.save_job(pipeline.normalize_posting(posting, source))
        results[outcome] += 1
    return results


def import_candidate_leads(database: Database, path: Path) -> dict[str, int]:
    """Import internal discovery leads without making them public jobs."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload if isinstance(payload, list) else [payload]
    registry = load_discovery_source_registry()
    known_discovery_sources = {str(item["id"]) for item in registry["sources"]}
    created = 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each lead must be a JSON object")
        discovery_source_id = str(item.get("discovery_source_id") or "").strip()
        if discovery_source_id and discovery_source_id not in known_discovery_sources:
            raise ValueError(
                f"Unknown discovery_source_id: {discovery_source_id}"
            )
        database.create_candidate_lead(item)
        created += 1
    return {"created": created, "public_jobs_created": 0}


def worker_health(database: Database, max_age_seconds: int) -> dict[str, Any]:
    heartbeat = database.get_service_heartbeat("worker")
    if heartbeat is None:
        return {
            "ok": False,
            "message": "尚未收到 worker 心跳。",
            "max_age_seconds": max_age_seconds,
        }
    try:
        updated_at = datetime.fromisoformat(
            heartbeat["updated_at"].replace("Z", "+00:00")
        )
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_seconds = max(
            0,
            int((datetime.now(timezone.utc) - updated_at).total_seconds()),
        )
    except (TypeError, ValueError):
        return {
            "ok": False,
            "message": "worker 心跳时间格式无效。",
            "heartbeat": heartbeat,
            "max_age_seconds": max_age_seconds,
        }
    ok = age_seconds <= max_age_seconds and heartbeat["status"] != "stopped"
    return {
        "ok": ok,
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
        "heartbeat": heartbeat,
    }


def coverage_snapshot_date(settings: Settings) -> str:
    """Return the local business date used for the replaceable daily snapshot."""
    return datetime.now(ZoneInfo(settings.timezone)).date().isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="地学就业信息站运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="初始化数据库和来源白名单")
    subparsers.add_parser("sync", help="立即同步已启用的公开来源")
    subparsers.add_parser("audit", help="审计岗位来源、阈值、日期和公告噪声")
    coverage_parser = subparsers.add_parser(
        "coverage",
        help="输出省份、来源健康、字段完整率和 100 人明确匹配覆盖报告",
    )
    coverage_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整 JSON 写入指定文件",
    )
    coverage_parser.add_argument(
        "--record",
        action="store_true",
        help="将当前质量指标写入当天可更新的快照，用于次日趋势比较",
    )
    organization_matrix_parser = subparsers.add_parser(
        "organization-matrix",
        help="输出组织层级、官方入口、备用入口和来源绑定状态",
    )
    organization_matrix_parser.add_argument(
        "--organization-role",
        choices=sorted(ORGANIZATION_ROLES),
        help="可选：按组织角色筛选",
    )
    organization_matrix_parser.add_argument(
        "--affiliation",
        help="可选：按所属体系精确筛选",
    )
    organization_matrix_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整 JSON 写入指定文件",
    )
    national_matrix_parser = subparsers.add_parser(
        "national-source-matrix",
        help="输出三桶油、国家管网及其重点单位的入口采集准入矩阵",
    )
    national_matrix_parser.add_argument(
        "--affiliation",
        help="可选：按所属体系筛选",
    )
    national_matrix_parser.add_argument(
        "--organization-role",
        choices=sorted(ORGANIZATION_ROLES),
        help="可选：按组织角色筛选",
    )
    national_matrix_parser.add_argument(
        "--runtime-status",
        choices=sorted(NATIONAL_RUNTIME_STATUSES),
        help="可选：按当前采集状态筛选",
    )
    national_matrix_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整 JSON 写入指定文件",
    )
    source_validation_parser = subparsers.add_parser(
        "source-validation-matrix",
        help="输出省级官方来源的样例、字段证据、备用入口和运行状态",
    )
    source_validation_parser.add_argument(
        "--province",
        choices=PROVINCES,
        help="可选：按省份筛选",
    )
    source_validation_parser.add_argument(
        "--role",
        choices=REQUIRED_ROLES,
        help="可选：按省级官方来源角色筛选",
    )
    source_validation_parser.add_argument(
        "--validation-stage",
        choices=sorted(SOURCE_VALIDATION_STAGES),
        help="可选：按核验阶段筛选",
    )
    source_validation_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整 JSON 写入指定文件",
    )
    source_health_parser = subparsers.add_parser(
        "source-health",
        help="轻量检查公开来源和 robots.txt，不采集岗位",
    )
    source_health_parser.add_argument("--source-id")
    source_health_parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="明确检查停用来源；未提供时只检查已启用来源",
    )
    subparsers.add_parser(
        "reindex-jobs",
        help="按当前分类和专业匹配规则重算历史岗位的派生字段",
    )
    sync_source_parser = subparsers.add_parser(
        "sync-source",
        help="立即同步一个已启用的公开来源",
    )
    sync_source_parser.add_argument("source_id")
    publish_parser = subparsers.add_parser("publish", help="立即生成一份日报")
    publish_parser.add_argument(
        "--send-email",
        action="store_true",
        help="日报生成后按 .env 邮件配置发送通知",
    )
    import_parser = subparsers.add_parser(
        "import-json",
        help="导入已人工核验的官方岗位 JSON 文件",
    )
    import_parser.add_argument("path", type=Path)
    import_leads_parser = subparsers.add_parser(
        "import-leads-json",
        help="导入第三方发现线索到私有候选池，不会发布到学生端",
    )
    import_leads_parser.add_argument("path", type=Path)
    list_leads_parser = subparsers.add_parser(
        "list-leads",
        help="列出私有候选线索，不会显示在公开网页",
    )
    list_leads_parser.add_argument("--status")
    list_leads_parser.add_argument("--discovery-source-id")
    list_leads_parser.add_argument("--province")
    subparsers.add_parser(
        "discovery-sources",
        help="输出私有发现来源注册表和线索归因统计",
    )
    subparsers.add_parser(
        "discovery-funnel",
        help="输出私有线索从发现到官方核验的转化漏斗",
    )
    purge_parser = subparsers.add_parser(
        "purge-source",
        help="仅删除一个来源的已采集岗位，保留来源配置",
    )
    purge_parser.add_argument("source_id")
    purge_parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认执行删除；未提供时只显示受影响岗位数量",
    )
    delete_job_parser = subparsers.add_parser(
        "delete-job",
        help="按精确岗位 ID 删除已确认的误采记录",
    )
    delete_job_parser.add_argument("job_id", type=int)
    delete_job_parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认执行删除；未提供时只显示该岗位的预览",
    )
    simulation_parser = subparsers.add_parser(
        "simulate-cohort",
        help="以匿名 100 人地球科学学院队列检查岗位覆盖",
    )
    simulation_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整模拟 JSON 写入指定文件",
    )
    worker_health_parser = subparsers.add_parser(
        "worker-health",
        help="检查 worker 最近心跳，供容器健康检查使用",
    )
    worker_health_parser.add_argument(
        "--max-age",
        type=int,
        default=180,
        help="允许的最大心跳年龄（秒，默认 180）",
    )
    subparsers.add_parser("worker", help="启动持续同步和 20:00 发布任务")
    artifact_process_parser = subparsers.add_parser(
        "process-artifact",
        help="受控下载并解析一个已登记的官方 PDF/Excel/CSV/DOCX 附件",
    )
    artifact_process_parser.add_argument("artifact_id", type=int)
    artifact_process_parser.add_argument(
        "--force-download",
        action="store_true",
        help="忽略已有下载文件并重新下载",
    )
    artifact_process_parser.add_argument(
        "--download-only",
        action="store_true",
        help="只下载并哈希，不提取表格或生成候选",
    )
    artifact_rows_parser = subparsers.add_parser(
        "list-artifact-rows",
        help="查看一个附件解析出的私有原始行",
    )
    artifact_rows_parser.add_argument("artifact_id", type=int)
    artifact_discover_parser = subparsers.add_parser(
        "discover-artifacts",
        help="从一个已核验官方公告页登记公开 PDF/Excel/CSV/DOCX 附件链接",
    )
    artifact_discover_parser.add_argument("source_id")
    artifact_discover_parser.add_argument("parent_url")
    artifact_candidate_parser = subparsers.add_parser(
        "list-artifact-candidates",
        help="查看官方附件派生的私有岗位候选",
    )
    artifact_candidate_parser.add_argument("--artifact-id", type=int)
    artifact_candidate_parser.add_argument("--status")

    args = parser.parse_args()
    if args.command == "worker":
        worker_main()
        return
    if args.command == "worker-health":
        settings = Settings.from_env()
        settings.ensure_runtime_paths()
        database = Database(settings.database_path)
        database.initialize()
        if args.max_age < 1:
            parser.error("--max-age 必须大于 0")
        result = worker_health(database, args.max_age)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["ok"]:
            raise SystemExit(1)
        return

    settings, database, pipeline = services()
    if args.command == "organization-matrix":
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
            organization_role=args.organization_role,
            affiliation=args.affiliation,
        )
        for row in rows:
            for channel in row["channels"]:
                source_id = channel.get("source_id")
                if not source_id:
                    channel["source_health_status"] = None
                    channel["latest_crawl_status"] = None
                    continue
                channel["source_health_status"] = source_health.get(
                    str(source_id), {}
                ).get("status")
                channel["latest_crawl_status"] = latest_runs.get(
                    str(source_id), {}
                ).get("status")
        result = {
            "summary": organization_matrix_summary(
                registry,
                source_records=sources,
            ),
            "filters": {
                "organization_role": args.organization_role,
                "affiliation": args.affiliation,
            },
            "items": rows,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "national-source-matrix":
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
            affiliation=args.affiliation,
            organization_role=args.organization_role,
            runtime_status=args.runtime_status,
        )
        result = {
            "summary": national_source_matrix_summary(
                matrix,
                organization_registry=registry,
                source_records=sources,
                source_health=source_health,
                latest_runs=latest_runs,
            ),
            "filters": {
                "affiliation": args.affiliation,
                "organization_role": args.organization_role,
                "runtime_status": args.runtime_status,
            },
            "items": rows,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "source-validation-matrix":
        registry = load_source_validation_registry()
        sources = database.list_sources()
        source_health = {
            item["source_id"]: item for item in database.list_source_health()
        }
        latest_runs = {
            item["source_id"]: item for item in database.list_latest_crawl_runs()
        }
        rows = source_validation_matrix_rows(
            registry,
            source_records=sources,
            province=args.province,
            role=args.role,
            validation_stage=args.validation_stage,
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
        result = {
            "summary": source_validation_summary(
                registry,
                source_records=sources,
            ),
            "filters": {
                "province": args.province,
                "role": args.role,
                "validation_stage": args.validation_stage,
            },
            "items": rows,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "init":
        print(
            json.dumps(
                {
                    "database": str(settings.database_path),
                    "registered_sources": len(database.list_sources()),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "process-artifact":
        processor = OfficialAttachmentProcessor(settings, database)
        try:
            result = processor.process(
                args.artifact_id,
                force_download=args.force_download,
                extract=not args.download_only,
            )
        except (AttachmentProcessingError, ValueError) as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    if args.command == "discover-artifacts":
        processor = OfficialAttachmentProcessor(settings, database)
        try:
            artifacts = processor.discover_from_page(
                args.source_id,
                args.parent_url,
            )
        except (AttachmentProcessingError, ValueError) as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(
            json.dumps(
                {"count": len(artifacts), "items": artifacts},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "list-artifact-rows":
        if database.get_source_artifact(args.artifact_id) is None:
            parser.error(f"artifact_id is not present: {args.artifact_id}")
        print(
            json.dumps(
                {"items": database.list_source_artifact_rows(args.artifact_id)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "list-artifact-candidates":
        print(
            json.dumps(
                {
                    "items": database.list_artifact_job_candidates(
                        artifact_id=args.artifact_id,
                        review_status=args.status,
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "coverage":
        snapshot_date = coverage_snapshot_date(settings)
        result = build_coverage_report(database, snapshot_date=snapshot_date)
        if args.record:
            database.save_coverage_snapshot(snapshot_date, result)
            result["snapshot_recorded"] = snapshot_date
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "source-health":
        if args.source_id:
            requested = database.get_source(args.source_id)
            if requested is None:
                parser.error(f"source_id is not registered: {args.source_id}")
            sources = [requested]
        elif args.include_disabled:
            sources = database.list_sources()
        else:
            sources = database.list_sources(enabled_only=True)
        probe = SourceHealthProbe(settings)
        results = []
        for source in sources:
            result = probe.check(source)
            database.record_source_health(
                source["id"],
                status=result.status,
                detail=result.detail,
                status_code=result.status_code,
                successful=result.successful,
            )
            results.append(
                {
                    "source_id": source["id"],
                    "status": result.status,
                    "status_code": result.status_code,
                    "detail": result.detail,
                }
            )
        print(
            json.dumps(
                {"checked": len(results), "sources": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "sync":
        summary = pipeline.sync_all()
        snapshot_date = coverage_snapshot_date(settings)
        database.save_coverage_snapshot(
            snapshot_date,
            build_coverage_report(database, snapshot_date=snapshot_date),
        )
        print(
            json.dumps(
                {"sync": summary.as_dict(), "snapshot_recorded": snapshot_date},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "reindex-jobs":
        print(json.dumps(pipeline.reindex_jobs(), ensure_ascii=False, indent=2))
        return
    if args.command == "audit":
        print(json.dumps(audit_database(database, settings), ensure_ascii=False, indent=2))
        return
    if args.command == "sync-source":
        source = database.get_source(args.source_id)
        if source is None:
            parser.error(f"source_id is not registered: {args.source_id}")
        if not source["enabled"]:
            parser.error(f"source_id is disabled: {args.source_id}")
        result = pipeline.sync_source(source)
        snapshot_date = coverage_snapshot_date(settings)
        database.save_coverage_snapshot(
            snapshot_date,
            build_coverage_report(database, snapshot_date=snapshot_date),
        )
        print(
            json.dumps(
                {**result.__dict__, "snapshot_recorded": snapshot_date},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "publish":
        audit = audit_database(database, settings)
        if not audit["ok"]:
            print(
                json.dumps(
                    {
                        "error": "日报发布前的数据审计未通过。",
                        "audit": audit,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1)
        report = publish_daily_report(database, settings)
        result: dict[str, Any] = {
            "report_date": report["report_date"],
            "stats": report["stats"],
        }
        if args.send_email:
            status = Mailer(settings).send_daily_report(report)
            database.update_delivery_status(report["report_date"], status)
            result["email"] = status
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "import-json":
        print(
            json.dumps(
                import_verified_jobs(database, pipeline, args.path),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "import-leads-json":
        print(
            json.dumps(
                import_candidate_leads(database, args.path),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "list-leads":
        print(
            json.dumps(
                {
                    "items": database.list_candidate_leads(
                        args.status,
                        discovery_source_id=args.discovery_source_id,
                        province=args.province,
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "discovery-sources":
        registry = load_discovery_source_registry()
        leads = database.list_candidate_leads(limit=5000)
        print(
            json.dumps(
                {
                    "summary": discovery_source_summary(registry, lead_rows=leads),
                    "items": discovery_source_rows(registry),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "discovery-funnel":
        registry = load_discovery_source_registry()
        leads = database.list_candidate_leads(limit=5000)
        print(json.dumps(discovery_funnel(leads, registry), ensure_ascii=False, indent=2))
        return
    if args.command == "simulate-cohort":
        jobs, _ = database.list_jobs(page_size=None)
        result = simulate_cohort(jobs)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        # Terminal output stays reviewable; --output retains the complete 100-row
        # anonymous evidence file when an operator needs person-level coverage.
        display_result = {
            key: result[key]
            for key in (
                "simulation",
                "input_open_jobs",
                "cohort_size",
                "distribution",
                "summary",
                "profiles",
            )
        }
        if args.output:
            display_result["output"] = str(args.output)
        print(json.dumps(display_result, ensure_ascii=False, indent=2))
        return
    if args.command == "delete-job":
        job = database.find_job(args.job_id)
        if job is None:
            parser.error(f"job_id is not present: {args.job_id}")
        result = {
            "job_id": job["id"],
            "source_id": job["source_id"],
            "title": job["title"],
            "source_url": job["source_url"],
            "deleted": False,
            "confirmed": bool(args.confirm),
        }
        if args.confirm:
            result["deleted"] = database.delete_job(args.job_id)
        else:
            result["message"] = "仅预览；添加 --confirm 后才会删除这一条精确岗位记录。"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "purge-source":
        source = database.get_source(args.source_id)
        if source is None:
            parser.error(f"source_id is not registered: {args.source_id}")
        existing_count = database.count_jobs_for_source(args.source_id)
        result = {
            "source_id": args.source_id,
            "source_name": source["name"],
            "matched_jobs": existing_count,
            "deleted_jobs": 0,
            "confirmed": bool(args.confirm),
        }
        if args.confirm:
            result["deleted_jobs"] = database.delete_jobs_for_source(args.source_id)
        else:
            result["message"] = "仅预览；添加 --confirm 后才会删除该来源的岗位。"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
