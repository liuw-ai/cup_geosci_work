from __future__ import annotations

import json

import pytest

from job_hub.db import Database
from scripts.capture_phase6_discovery_notice import capture_notice


def _registry(tmp_path):
    path = tmp_path / "discovery-sources.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "description": "test",
                "sources": [
                    {
                        "id": "offcn-discovery",
                        "name": "中公教育招聘公告汇总",
                        "source_type": "aggregator",
                        "homepage_url": "https://www.offcn.com/",
                        "access_mode": "public_page",
                        "status": "checked",
                        "scope": "test",
                        "publication_policy": "batch_candidate_allowed",
                        "trust_grade": "B",
                        "batch_candidate_allowed": True,
                        "official_domain_allowlist": [],
                        "last_checked_on": "2026-10-23",
                        "scan_config": {"allowed_hosts": ["www.offcn.com"]},
                        "notes": "test",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _evidence(tmp_path):
    root = tmp_path / "runtime" / "phase6" / "review"
    cache = root / "http-cache"
    cache.mkdir(parents=True)
    article = cache / "offcn.html"
    article.write_text("<html><title>招聘公告</title></html>", encoding="utf-8")
    robots = cache / "robots.txt"
    robots.write_text("User-agent: *\nDisallow: /offcn\n", encoding="utf-8")
    return root, article, robots


def test_phase6_notice_capture_remains_private_and_records_hashes(tmp_path) -> None:
    database_path = tmp_path / "runtime" / "phase6" / "review" / "job_hub.sqlite3"
    database_path.parent.mkdir(parents=True)
    database = Database(database_path)
    database.initialize()
    database.upsert_source(
        {
            "id": "sinopec-career",
            "name": "中国石化人才招聘网站",
                "publisher": "中国石油化工集团有限公司",
            "homepage_url": "https://job.sinopec.com/",
            "source_type": "manual",
            "category": "test",
            "source_tier": "A",
            "enabled": False,
            "config": {"allowed_hosts": ["job.sinopec.com"]},
        }
    )
    root, article, robots = _evidence(tmp_path)
    result = capture_notice(
        database=database,
        source_id="offcn-discovery",
        article_url="https://www.offcn.com/gqzp/2026/1022/258317.html",
        article_file=article,
        robots_file=robots,
        evidence_root=root,
        title="2027中国石油化工集团有限公司校园招聘公告",
        employer="中国石油化工集团有限公司",
        official_notice_url="https://job.sinopec.com/#/school/groupNoticeDetail?id=notice",
        official_source_id="sinopec-career",
        official_application_url="https://job.sinopec.com/",
        published_date="2026-09-24",
        deadline_date="2026-10-28",
        registry_path=_registry(tmp_path),
    )
    lead = result["lead"]
    assert lead["verification_status"] == "candidate"
    assert lead["official_domain_status"] == "registered_source_match"
    assert lead["official_application_url"] == "https://job.sinopec.com/"
    assert result["student_job_created"] is False
    capture = lead["metadata"]["phase6_discovery_capture"]
    assert capture["article"]["sha256"]
    assert capture["robots"]["robots_allowed"] is True
    assert capture["official_recovery"]["official_source_id"] == "sinopec-career"


def test_phase6_notice_capture_rejects_an_unallowlisted_article_host(tmp_path) -> None:
    database_path = tmp_path / "runtime" / "phase6" / "review" / "job_hub.sqlite3"
    database_path.parent.mkdir(parents=True)
    database = Database(database_path)
    database.initialize()
    root, article, robots = _evidence(tmp_path)
    with pytest.raises(ValueError, match="allowlisted"):
        capture_notice(
            database=database,
            source_id="offcn-discovery",
            article_url="https://untrusted.example/notice.html",
            article_file=article,
            robots_file=robots,
            evidence_root=root,
            title="地质岗位",
            employer="测试单位",
            official_notice_url="https://example.gov.cn/notice",
            official_source_id="official-source",
            registry_path=_registry(tmp_path),
        )
