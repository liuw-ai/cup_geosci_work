"""Capture one reviewed third-party recruitment notice into an isolated Phase 6 queue.

This command fills the gap between a lawful aggregator article and a downloadable
position table.  It preserves the original article bytes and its robots evidence,
then creates only a private ``candidate_lead``.  It cannot publish a job, cannot
derive an application URL from the aggregator, and rejects the formal runtime
database unless an explicit release-only override is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.db import Database
from job_hub.discovery import load_discovery_source_registry


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contained_relative(path: Path, root: Path, *, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"{label} must be retained below {root}") from error


def _http_host(value: str, *, label: str) -> str:
    parsed = urlparse(str(value).strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError(f"{label} must be an HTTP(S) URL")
    return host


def _source(source_id: str, registry_path: Path | None = None) -> dict[str, object]:
    registry = load_discovery_source_registry(registry_path or ROOT / "data" / "discovery_sources.json")
    source = next(
        (item for item in registry["sources"] if str(item.get("id") or "") == source_id),
        None,
    )
    if source is None:
        raise ValueError(f"Unknown discovery source: {source_id}")
    if source.get("trust_grade") != "B" or not source.get("batch_candidate_allowed"):
        raise ValueError("Only reviewed B-level batch-candidate discovery sources are allowed")
    if source.get("access_mode") != "public_page" or source.get("status") != "checked":
        raise ValueError("Discovery source is not a checked public-page source")
    return dict(source)


def capture_notice(
    *,
    database: Database,
    source_id: str,
    article_url: str,
    article_file: Path,
    robots_file: Path,
    evidence_root: Path,
    title: str,
    employer: str,
    official_notice_url: str,
    official_source_id: str,
    official_application_url: str | None = None,
    published_date: str | None = None,
    deadline_date: str | None = None,
    location: str | None = None,
    registry_path: Path | None = None,
    captured_at: str | None = None,
) -> dict[str, object]:
    """Create an idempotent private notice lead with durable file evidence."""

    source = _source(source_id, registry_path)
    article_host = _http_host(article_url, label="article_url")
    scan_config = source.get("scan_config") or {}
    allowed_hosts = {
        str(host).lower().rstrip(".")
        for host in scan_config.get("allowed_hosts", [])
    }
    if article_host not in allowed_hosts:
        raise ValueError("article_url host is not allowlisted for the discovery source")
    _http_host(official_notice_url, label="official_notice_url")
    if official_application_url:
        _http_host(official_application_url, label="official_application_url")

    article_file = article_file.resolve()
    robots_file = robots_file.resolve()
    evidence_root = evidence_root.resolve()
    article_relative = _contained_relative(article_file, evidence_root, label="article_file")
    robots_relative = _contained_relative(robots_file, evidence_root, label="robots_file")
    if not article_file.is_file() or not robots_file.is_file():
        raise ValueError("article_file and robots_file must exist")
    article_bytes = article_file.stat().st_size
    if article_bytes <= 0:
        raise ValueError("article_file must not be empty")
    robots_text = robots_file.read_text(encoding="utf-8", errors="replace")
    if "User-agent:" not in robots_text:
        raise ValueError("robots_file does not contain a robots policy")
    captured_at = captured_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata = {
        "phase6_discovery_capture": {
            "version": 1,
            "capture_kind": "third_party_notice",
            "source_id": source_id,
            "article": {
                "url": article_url,
                "retained_path": article_relative,
                "media_type": mimetypes.guess_type(article_file.name)[0] or "text/html",
                "byte_count": article_bytes,
                "sha256": _sha256(article_file),
                "captured_at": captured_at,
            },
            "robots": {
                "retained_path": robots_relative,
                "sha256": _sha256(robots_file),
                "policy_summary": "User-agent:* only disallows /offcn; reviewed article is outside that path.",
                "robots_allowed": True,
            },
            "official_recovery": {
                "official_notice_url": official_notice_url,
                "official_source_id": official_source_id,
                "official_application_url": official_application_url or None,
                "method": "explicit_original_link_or_official_site_statement_in_aggregator_article",
                "publication_gate": "official_content_verified_required",
            },
        },
        "deadline_date_hint": deadline_date or None,
    }
    lead = database.create_candidate_lead(
        {
            "lead_provider": str(source["name"]),
            "lead_url": article_url,
            "title": title,
            "employer_hint": employer,
            "location_hint": location,
            "published_date": published_date,
            "official_url": official_notice_url,
            "official_source_id": official_source_id,
            "official_application_url": official_application_url,
            "discovery_source_id": source_id,
            "trust_grade": "B",
            "verification_note": (
                "Phase 6 可审计第三方公告捕获：已回溯官方公告/招聘站，但尚未取得岗位级"
                "专业、学历、地点和官方内容核验，保持私有候选。"
            ),
            "metadata": metadata,
        }
    )
    return {
        "lead": lead,
        "publication_gate": "official_content_verified_required",
        "student_job_created": False,
        "formal_database_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--article-url", required=True)
    parser.add_argument("--article-file", required=True, type=Path)
    parser.add_argument("--robots-file", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--employer", required=True)
    parser.add_argument("--official-notice-url", required=True)
    parser.add_argument("--official-source-id", required=True)
    parser.add_argument("--official-application-url")
    parser.add_argument("--published-date")
    parser.add_argument("--deadline-date")
    parser.add_argument("--location")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-formal-database", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        parser.error("this evidence-preserving command requires --apply to the explicit isolated database")
    database_path = args.database.resolve()
    formal_database = (ROOT / "runtime" / "job_hub.sqlite3").resolve()
    if database_path == formal_database and not args.allow_formal_database:
        parser.error("refusing to write runtime/job_hub.sqlite3; choose an isolated Phase 6 review database")
    phase6_root = (ROOT / "runtime" / "phase6").resolve()
    _contained_relative(database_path, phase6_root, label="database")
    database = Database(database_path)
    database.initialize()
    result = capture_notice(
        database=database,
        source_id=args.source_id,
        article_url=args.article_url,
        article_file=args.article_file,
        robots_file=args.robots_file,
        evidence_root=args.evidence_root,
        title=args.title,
        employer=args.employer,
        official_notice_url=args.official_notice_url,
        official_source_id=args.official_source_id,
        official_application_url=args.official_application_url,
        published_date=args.published_date,
        deadline_date=args.deadline_date,
        location=args.location,
        registry_path=args.registry,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
