from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from job_hub.audit import audit_database
from job_hub.browser_capture import (
    BrowserCaptureError,
    browser_capture_summary,
    load_browser_capture,
    run_browser_capture,
)
from job_hub.attachments import (
    AttachmentProcessingError,
    OfficialAttachmentProcessor,
    build_attachment_field_evidence,
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
from job_hub.government_positions import (
    government_position_quality_report,
    load_position_registry,
)
from job_hub.government_artifacts import (
    load_government_artifact_manifest,
    register_government_artifacts,
)
from job_hub.domestic_expansion import (
    QUEUE_STATUSES,
    domestic_expansion_rows,
    domestic_expansion_summary,
    load_domestic_expansion_queue,
)
from job_hub.discovery import (
    discovery_funnel,
    discovery_source_rows,
    discovery_source_summary,
    load_discovery_source_registry,
)
from job_hub.entry_probes import (
    PublicEntryProbeRunner,
    load_national_entry_targets,
    national_entry_probe_summary,
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
from job_hub.national_probes import (
    load_national_source_probes,
    national_source_probe_summary,
)
from job_hub.reports import publish_daily_report
from job_hub.simulation import simulate_cohort
from job_hub.sinopec import load_sinopec_capture, sinopec_capture_summary
from job_hub.source_targets import REQUIRED_ROLES
from job_hub.source_validation import (
    load_source_validation_registry,
    source_validation_matrix_rows,
    source_validation_summary,
)
from job_hub.sources import RawPosting, SourceHealthProbe
from job_hub.transport import TRANSPORT_MODES, validate_transport_mode
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
    source_id_override: str | None = None,
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
        source_id = str(
            source_id_override or item.get("source_id") or "official-manual-import"
        ).strip()
        source = database.get_source(source_id)
        if source is None:
            raise ValueError(f"source_id is not registered in data/sources.json: {source_id}")
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
            match_text=str(item.get("match_text", "")).strip() or None,
            qualification_text=str(item.get("qualification_text", "")).strip() or None,
            field_evidence=(
                item.get("field_evidence")
                if isinstance(item.get("field_evidence"), dict)
                else None
            ),
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
    national_probe_parser = subparsers.add_parser(
        "national-source-probes",
        help="输出国家能源体系公开入口/API探测证据",
    )
    national_probe_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整 JSON 写入指定文件",
    )
    entry_probe_parser = subparsers.add_parser(
        "national-entry-probe",
        help="只读探测国家能源体系官方招聘入口及备用入口",
    )
    entry_probe_parser.add_argument(
        "--system",
        help="可选：只探测 cnpc、sinopec、cnooc 或 pipechina",
    )
    entry_probe_parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="单次请求超时秒数（默认 10）",
    )
    entry_probe_parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="临时网络错误最大重试次数（默认 1）",
    )
    entry_probe_parser.add_argument(
        "--environment",
        default="local-network",
        help="探测环境标签，例如 local-network 或 production-server",
    )
    entry_probe_parser.add_argument(
        "--transport",
        choices=sorted(TRANSPORT_MODES),
        default=None,
        help="HTTP 出站模式；默认读取 HTTP_TRANSPORT_MODE（否则 environment）",
    )
    entry_probe_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整探测结果写入指定 JSON 文件",
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
    government_position_parser = subparsers.add_parser(
        "government-position-audit",
        help="审计事业编与公务员职位表的官方证据、有效期和专业匹配",
    )
    government_position_parser.add_argument(
        "--path",
        type=Path,
        default=Path("data/government_position_registry.json"),
        help="政府职位表证据台账 JSON",
    )
    government_position_parser.add_argument(
        "--today",
        help="覆盖审计日期，格式 YYYY-MM-DD",
    )
    government_position_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整 JSON 写入指定文件",
    )
    government_artifact_parser = subparsers.add_parser(
        "register-government-artifacts",
        help="将省级官方职位表附件登记到私有受控下载队列",
    )
    government_artifact_parser.add_argument(
        "--path",
        type=Path,
        default=Path("data/government_artifact_manifest.json"),
        help="政府职位表附件清单 JSON",
    )
    domestic_queue_parser = subparsers.add_parser(
        "domestic-expansion-queue",
        help="输出三桶油、地勘、事业编和公务员官方来源扩展队列",
    )
    domestic_queue_parser.add_argument("--system", help="按体系筛选，例如中国石油或公务员")
    domestic_queue_parser.add_argument(
        "--status",
        choices=sorted(QUEUE_STATUSES),
        help="按来源当前证据状态筛选",
    )
    domestic_queue_parser.add_argument(
        "--output",
        type=Path,
        help="可选：将完整队列 JSON 写入指定文件",
    )
    sinopec_capture_parser = subparsers.add_parser(
        "sinopec-capture",
        help="输出中石化官方 SPA 捕获快照的单位和岗位扫描统计",
    )
    sinopec_capture_parser.add_argument(
        "--path",
        type=Path,
        default=Path("data/verified/sinopec-geoscience-20260924.json"),
        help="版本化浏览器捕获 JSON 路径",
    )
    sinopec_capture_parser.add_argument(
        "--require-complete",
        action="store_true",
        help="要求捕获文件已包含全部 132 个单位",
    )
    browser_capture_parser = subparsers.add_parser(
        "browser-capture-check",
        help="校验服务器浏览器生成的动态官方岗位捕获清单",
    )
    browser_capture_parser.add_argument("source_id", help="已注册的 official_browser_rows 来源")
    browser_capture_parser.add_argument(
        "--path", type=Path, help="可选：覆盖来源配置中的捕获文件路径"
    )
    browser_capture_parser.add_argument(
        "--max-age-hours", type=float, help="可选：覆盖来源配置中的捕获有效期"
    )
    browser_capture_run_parser = subparsers.add_parser(
        "browser-capture-run",
        help="在服务器公开 Chromium 中执行一次动态官方岗位捕获",
    )
    browser_capture_run_parser.add_argument("source_id", help="已注册的 official_browser_rows 来源")
    browser_capture_run_parser.add_argument(
        "--output", type=Path, help="可选：覆盖来源配置中的输出路径"
    )
    browser_capture_run_parser.add_argument(
        "--url", help="可选：覆盖来源配置中的 browser_url"
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
    source_tasks_parser = subparsers.add_parser(
        "source-tasks",
        help="查看来源队列的待执行、失败、阻断和租约状态",
    )
    source_tasks_parser.add_argument(
        "--due-only",
        action="store_true",
        help="只显示当前到期且未被其他 worker 租用的任务",
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
    publish_parser.add_argument(
        "--refresh",
        action="store_true",
        help="重建当天日报快照；用于补录已核验岗位后更新当前日期页面",
    )
    import_parser = subparsers.add_parser(
        "import-json",
        help="导入已人工核验的官方岗位 JSON 文件",
    )
    import_parser.add_argument("path", type=Path)
    import_parser.add_argument(
        "--source-id",
        help="覆盖 JSON 中的 source_id；适用于由来源注册表绑定的官方快照文件",
    )
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
    subparsers.add_parser(
        "repair-attachment-evidence",
        help="把历史附件候选迁移到统一岗位级证据格式并重算发布门禁",
    )

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
    if args.command == "national-entry-probe":
        if args.timeout < 1:
            parser.error("--timeout 必须大于 0")
        if args.retries < 0 or args.retries > 4:
            parser.error("--retries 必须在 0 到 4 之间")
        try:
            transport_mode = validate_transport_mode(
                args.transport or os.getenv("HTTP_TRANSPORT_MODE", "environment")
            )
        except ValueError as error:
            parser.error(str(error))
        targets = load_national_entry_targets()
        result = PublicEntryProbeRunner(
            timeout_seconds=args.timeout,
            retries=args.retries,
            transport_mode=transport_mode,
        ).run(
            targets,
            system_id=args.system,
            environment=args.environment,
        )
        output = {
            "summary": national_entry_probe_summary(result),
            "systems": result["systems"],
            "attempts": result["attempts"],
            "observed_on": result["observed_on"],
            "environment": result["environment"],
            "transport_mode": result["transport_mode"],
            "proxy_environment_present": result["proxy_environment_present"],
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        # Console encodings on Windows may not represent arbitrary upstream
        # error text; the JSON file remains UTF-8 with native Chinese text.
        print(json.dumps(output, ensure_ascii=True, indent=2))
        return
    if args.command == "domestic-expansion-queue":
        queue = load_domestic_expansion_queue()
        result = {
            "summary": domestic_expansion_summary(queue),
            "filters": {"system": args.system, "status": args.status},
            "items": domestic_expansion_rows(
                queue,
                system=args.system,
                status=args.status,
            ),
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
    if args.command == "national-source-probes":
        probes = load_national_source_probes()
        result = {
            "summary": national_source_probe_summary(probes),
            "items": probes["probes"],
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "sinopec-capture":
        payload = load_sinopec_capture(
            args.path,
            require_complete_manifest=args.require_complete,
        )
        result = {
            "summary": sinopec_capture_summary(payload),
            "platform_url": payload["platform_url"],
            "captured_at": payload["captured_at"],
            "source_policy": (
                "浏览器捕获快照仅在单位清单、岗位详情、专业和学历证据完整后，"
                "才允许进入学生端发布门禁。"
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "browser-capture-check":
        source = database.get_source(args.source_id)
        if source is None:
            parser.error(f"source_id is not registered: {args.source_id}")
        if source.get("source_type") != "official_browser_rows":
            parser.error("browser-capture-check requires an official_browser_rows source")
        config = source["config"]
        capture_path = args.path or (settings.data_dir / str(config["capture_path"]))
        try:
            payload = load_browser_capture(
                capture_path,
                allowed_hosts=list(config["allowed_hosts"]),
                max_age_hours=args.max_age_hours
                if args.max_age_hours is not None
                else float(config.get("max_age_hours", 30)),
                require_complete_scan=bool(config.get("require_complete_scan", True)),
            )
        except BrowserCaptureError as error:
            print(
                json.dumps(
                    {"error": str(error), "source_id": args.source_id},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1)
        print(
            json.dumps(
                {
                    "source_id": args.source_id,
                    "capture_path": str(capture_path),
                    "summary": browser_capture_summary(payload),
                    "publication_policy": "normal student publication gate remains mandatory",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "browser-capture-run":
        source = database.get_source(args.source_id)
        if source is None:
            parser.error(f"source_id is not registered: {args.source_id}")
        if source.get("source_type") != "official_browser_rows":
            parser.error("browser-capture-run requires an official_browser_rows source")
        config = source["config"]
        output = args.output or (settings.data_dir / str(config["capture_path"]))
        browser_url = args.url or str(config.get("browser_url") or source["homepage_url"])
        try:
            payload = run_browser_capture(
                url=browser_url,
                output=output,
                config=config,
                allowed_hosts=list(config["allowed_hosts"]),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
            )
        except BrowserCaptureError as error:
            print(json.dumps({"error": str(error), "source_id": args.source_id}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(
            json.dumps(
                {
                    "source_id": args.source_id,
                    "output": str(output),
                    "summary": browser_capture_summary(payload),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
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
    if args.command == "government-position-audit":
        try:
            registry = load_position_registry(args.path)
            result = government_position_quality_report(registry, today=args.today)
        except (OSError, ValueError) as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "register-government-artifacts":
        try:
            manifest = load_government_artifact_manifest(args.path)
            registered = register_government_artifacts(database, manifest)
        except (OSError, ValueError) as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(
            json.dumps(
                {
                    "manifest": str(args.path),
                    "registered": len(registered),
                    "items": registered,
                    "next_step": "在服务器运行 process-artifact <artifact_id>；解析结果仍需人工复核后发布。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
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
        report = publish_daily_report(
            database,
            settings,
            force_refresh=bool(args.refresh),
        )
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
                import_verified_jobs(
                    database,
                    pipeline,
                    args.path,
                    source_id_override=args.source_id,
                ),
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
    if args.command == "repair-attachment-evidence":
        candidates = database.list_artifact_job_candidates(limit=500)
        candidate_updates = 0
        job_updates = 0
        for candidate in candidates:
            existing = candidate.get("field_evidence") or {}
            if not isinstance(existing, dict):
                existing = {}
            nested = existing.get("fields")
            if not isinstance(nested, dict):
                nested = {}
            evidence = build_attachment_field_evidence(
                title=str(candidate.get("title") or ""),
                major=str(existing.get("专业范围") or nested.get("major") or "") or None,
                degree=str(existing.get("学历要求") or nested.get("degree") or "") or None,
                location=str(candidate.get("location") or "").strip() or None,
                artifact=candidate,
                row={
                    "sheet_name": candidate.get("row_sheet_name"),
                    "row_number": candidate.get("row_number"),
                },
                row_text=str(candidate.get("row_text") or ""),
            )
            if database.update_artifact_candidate_field_evidence(
                int(candidate["id"]), evidence
            ):
                candidate_updates += 1
            published_job_id = candidate.get("published_job_id")
            if published_job_id and database.update_job_field_evidence(
                int(published_job_id), evidence
            ):
                job_updates += 1
        reindex = pipeline.reindex_jobs()
        print(
            json.dumps(
                {
                    "candidate_updates": candidate_updates,
                    "job_updates": job_updates,
                    "reindex": reindex,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "source-tasks":
        tasks = database.list_source_tasks(due_only=args.due_only)
        counts: dict[str, int] = {}
        for task in tasks:
            status = str(task.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        print(
            json.dumps(
                {
                    "summary": {
                        "count": len(tasks),
                        "by_status": counts,
                        "due_only": bool(args.due_only),
                    },
                    "items": tasks,
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
        job = database.find_job(args.job_id, student_visible=False)
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
