from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from job_hub.app import create_app
from job_hub.contracts import ContractValidationError, validate_discovery_source_registry
from job_hub.db import Database, SCHEMA
from job_hub.discovery import (
    discovery_funnel,
    discovery_link_quality_category,
    discovery_link_quality_reason,
    is_navigation_discovery_link,
    is_non_vacancy_discovery_link,
    load_discovery_source_registry,
    normalize_lead_url,
    official_candidate_quality,
    official_domain_assessment,
    reconcile_navigation_discovery_leads,
    recover_persisted_official_url_candidates,
    resolve_official_url_candidates,
)
from job_hub.discovery_scan import (
    DiscoveryAccessLimited,
    DiscoveryScanner,
    DiscoveryScanItem,
    _host_allowed,
    _matches_url_patterns,
    _optional_text_patterns,
    _response_text,
    _url_patterns,
    extract_discovery_evidence,
    enrich_persisted_candidate_leads,
    run_discovery_scan,
)

from conftest import make_settings, source


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_discovery_scan_decodes_declared_chinese_charset() -> None:
    class Response:
        content = '<meta charset="gb2312"><a>地质工程招聘</a>'.encode("gb2312")
        encoding = "ISO-8859-1"
        apparent_encoding = "GB2312"

    assert "地质工程招聘" in _response_text(Response())


def test_navigation_links_do_not_enter_private_lead_funnel() -> None:
    assert is_navigation_discovery_link(
        "http://www.huatu.com/z/gkbmrk/", "国家公务员考试报名入口"
    )
    assert is_navigation_discovery_link(
        "http://www.huatu.com/gwy/", "各省公务员"
    )
    assert is_navigation_discovery_link(
        "https://bm.scs.gov.cn/pp/gkweb/core/web/ui/business/home/gkhome.html",
        "准考证打印入口",
    )
    assert is_navigation_discovery_link(
        "http://www.huatu.com/z/gkksfy/", "国家公务员考试费用"
    )
    assert is_navigation_discovery_link(
        "http://www.huatu.com/z/gkbmqr/", "国家公务员考试报名确认"
    )
    assert not is_navigation_discovery_link(
        "https://www.huatu.com/2026/0901/2872149.html",
        "10月每日公职招聘公告汇总",
    )


def test_exam_content_links_do_not_enter_private_lead_funnel() -> None:
    assert is_non_vacancy_discovery_link(
        "https://www.huatu.com/2026/0203/2849338.html",
        "2026辽宁省锦州公务员报名人数统计分析：48个岗位无人报名",
    )
    assert is_non_vacancy_discovery_link(
        "https://www.huatu.com/shenlun/20240314/2735659.html",
        "2024年公务员考试申论热点：读懂一号文件",
    )
    assert is_non_vacancy_discovery_link(
        "https://www.huatu.com/2025/0721/2825627.html",
        "2025德州市直机关遴选公务员面试人员名单",
    )
    assert discovery_link_quality_reason(
        "https://www.huatu.com/2025/1110/2839221.html",
        "2026年北京公务员职位表下载（1690个岗位）",
    ) is None
    assert discovery_link_quality_reason(
        "https://www.huatu.com/2026/0203/2849338.html",
        "2026辽宁省锦州公务员报名人数统计分析",
    ) == "non_vacancy_content_v1"


@pytest.mark.parametrize(
    ("url", "title", "category"),
    [
        ("http://www.huatu.com/jlgwy/", "吉林省公务员考试", "navigation"),
        (
            "http://www.huatu.com/2026/0413/2859050.html",
            "安徽黄山市2026年度考试录用公务员笔试合格分数",
            "result_or_publicity",
        ),
        (
            "http://www.huatu.com/2025/0315/2806273.html",
            "2025省公务员考试难度分析及考点归纳",
            "training_or_ad",
        ),
        (
            "http://www.huatu.com/z/gkzwb/",
            "国家公务员职位表",
            "navigation",
        ),
        (
            "http://www.huatu.com/2024/0106/2718424.html",
            "2024年山西省公务员考试招录4936人",
            "historical",
        ),
    ],
)
def test_discovery_quality_categories_explain_private_rejections(
    url: str, title: str, category: str
) -> None:
    assert discovery_link_quality_category(url, title) == category
    assert discovery_link_quality_reason(url, title) is not None


def test_recruitment_roundups_remain_private_recovery_clues() -> None:
    assert discovery_link_quality_category(
        "https://www.huatu.com/2026/0901/2872149.html",
        "10月每日公职招聘公告汇总",
    ) is None
    assert discovery_link_quality_category(
        "https://sydw.huatu.com/2026/1010/2874692.html",
        "2026年中国农业科学院草原研究所第三批统一公开招聘工作人员5人公告",
    ) is None


def test_navigation_lead_reconciliation_is_reversible_private_rejection(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    lead = database.create_candidate_lead(
        {
            "lead_provider": "华图教育",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://www.huatu.com/z/gkbmrk/",
            "title": "国家公务员考试报名入口",
        }
    )

    result = reconcile_navigation_discovery_leads(database)

    assert result["rejected"] == 1
    saved = database.get_candidate_lead(lead["id"])
    assert saved["verification_status"] == "rejected"
    assert saved["published_job_id"] is None
    assert saved["metadata"]["quality_filter"]["rule"] == "navigation_link_v1"


def test_discovery_scan_allows_only_registered_first_party_subdomains() -> None:
    allowed = {"www.huatu.com", "sydw.huatu.com"}
    assert _host_allowed("https://sydw.huatu.com/zt/sydwggk/", allowed)
    assert _host_allowed("https://www.huatu.com/2026/0901/2872149.html", allowed)
    assert not _host_allowed("https://v.huatu.com/htzx/course.shtml?category=1", allowed)
    assert not _host_allowed("https://example.com/jobs/1", allowed)


def test_discovery_lead_url_patterns_exclude_navigation_before_private_lead_creation() -> None:
    patterns = _url_patterns(
        [r"^https://(?:www\.)?offcn\.com/gqzp/20\d{2}/\d{4}/\d+\.html$"]
    )
    assert _matches_url_patterns(
        "https://www.offcn.com/gqzp/2026/1022/258360.html", patterns
    )
    assert not _matches_url_patterns("https://www.offcn.com/gqzp/", patterns)
    assert not _matches_url_patterns(
        "https://www.offcn.com/gqzp/shiyou/", patterns
    )


def test_discovery_lead_url_patterns_reject_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="invalid lead_url_patterns"):
        _url_patterns(["["])


def test_discovery_program_relevance_patterns_are_optional_and_validated() -> None:
    assert _optional_text_patterns(None, field="program_relevance_patterns") == ()
    assert _optional_text_patterns(["地质", "石油"], field="program_relevance_patterns") == (
        "地质",
        "石油",
    )
    with pytest.raises(ValueError, match="invalid program_relevance_patterns"):
        _optional_text_patterns(["["], field="program_relevance_patterns")


def test_discovery_announcement_title_filter_excludes_portal_and_faq_pages(tmp_path) -> None:
    scanner = DiscoveryScanner(make_settings(tmp_path))
    source = {"id": "offcn-discovery", "name": "中公教育招聘公告汇总"}
    base_url = "https://www.offcn.com/gqzp/"
    article_patterns = _url_patterns(
        [r"^https://(?:www\.)?offcn\.com/gqzp/20\d{2}/\d{4}/\d+\.html$"]
    )
    announcement_patterns = _optional_text_patterns(
        ["招聘公告", "招录公告", "公开招聘", "招聘简章", r"(?:20\d{2}年|20\d{2}届).*校园招聘"],
        field="announcement_title_patterns",
    )
    body = """
    <a href="/gqzp/2026/1022/258317.html">2027中国石油化工集团有限公司校园招聘公告</a>
    <a href="/gqzp/2026/0909/255014.html">中国石油招聘官网入口</a>
    <a href="/gqzp/2026/1009/257219.html">2027中国海洋石油秋季校园招聘常见问题</a>
    """

    leads = scanner._html_leads(
        source,
        base_url,
        body,
        ("招聘", "校园", "校招", "石油"),
        ("常见问题", "官网", "入口", "平台", "网站", "查看", "下载", "流程"),
        20,
        {"www.offcn.com", "offcn.com"},
        article_patterns,
        ("石油",),
        announcement_patterns,
    )

    assert [lead["title"] for lead in leads] == ["2027中国石油化工集团有限公司校园招聘公告"]


def test_discovery_evidence_extracts_attribution_and_attachments_without_promotion() -> None:
    html = (
        "<html><head><title>\u4e8b\u4e1a\u5355\u4f4d\u62db\u8058\u516c\u544a</title></head><body>"
        "<span>\u6587\u7ae0\u6765\u6e90\uff1a\u4e1c\u65b9\u5e02\u4eba\u6c11\u653f\u5e9c</span>"
        "<a href=\"/uploads/jobs.xlsx\">\u5c97\u4f4d\u8868.xlsx</a>"
        "<a href=\"https://www.dongfang.gov.cn/notice/1.html\">\u5b98\u65b9\u516c\u544a</a>"
        "<a href=\"https://ad.example.com/course\">\u62db\u8058\u8bfe\u7a0b</a>"
        "</body></html>"
    )
    evidence = extract_discovery_evidence(
        html,
        "https://sydw.huatu.com/2026/1010/1.html",
        allowed_hosts={"sydw.huatu.com"},
    )
    assert evidence["status"] == "fetched"
    assert evidence["source_attribution"] == "\u4e1c\u65b9\u5e02\u4eba\u6c11\u653f\u5e9c"
    assert evidence["attachment_candidates"][0]["url"].endswith("/uploads/jobs.xlsx")
    assert evidence["official_link_candidates"] == [
        {
            "url": "https://www.dongfang.gov.cn/notice/1.html",
            "label": "\u5b98\u65b9\u516c\u544a",
            "host": "www.dongfang.gov.cn",
        }
    ]
    assert "verification" not in evidence


def test_discovery_evidence_recognizes_registered_company_domains_without_keyword_labels() -> None:
    """CNPC/Sinopec domains must not depend on a lucky anchor label."""
    html = (
        "<html><body>"
        "<a href='https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=1'>查看详情</a>"
        "<a href='https://job.sinopec.com/#/school/recruitmentPositions'>进入招聘</a>"
        "<a href='https://unknown.example/jobs/1'>查看详情</a>"
        "</body></html>"
    )
    evidence = extract_discovery_evidence(
        html,
        "https://sydw.huatu.com/2026/1010/2.html",
        allowed_hosts={"sydw.huatu.com"},
        official_hosts={"zhaopin.cnpc.com.cn", "job.sinopec.com"},
    )
    assert [item["url"] for item in evidence["official_link_candidates"]] == [
        "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=1",
        "https://job.sinopec.com/",
    ]


def test_discovery_evidence_recognizes_bare_registered_official_domain() -> None:
    """Official domains printed in prose become private candidates only."""

    evidence = extract_discovery_evidence(
        "<p>研究所官网：www.example.edu.cn/recruit/2026</p>",
        "https://sydw.huatu.com/2026/1012/3.html",
        allowed_hosts={"sydw.huatu.com"},
        official_hosts={"www.example.edu.cn"},
    )

    assert evidence["official_link_candidates"] == [
        {
            "url": "https://www.example.edu.cn/recruit/2026",
            "label": "正文登记的官方域名",
            "host": "www.example.edu.cn",
        }
    ]


def test_registered_official_host_collection_includes_disabled_fallback_sources() -> None:
    from job_hub.discovery_scan import _registered_official_hosts

    hosts = _registered_official_hosts(
        [
            {
                "homepage_url": "https://www.cnpc.com.cn/zh/rczp/",
                "config": {"allowed_hosts": ["zhaopin.cnpc.com.cn"]},
            },
            {
                "homepage_url": "https://job.sinopec.com/",
                "enabled": False,
            },
        ]
    )
    assert {"www.cnpc.com.cn", "zhaopin.cnpc.com.cn", "job.sinopec.com"} <= hosts


def test_discovery_registry_has_explicit_batch_candidate_policy() -> None:
    registry = load_discovery_source_registry()
    assert len(registry["sources"]) == 12
    batch_sources = {
        item["id"] for item in registry["sources"]
        if item["publication_policy"] == "batch_candidate_allowed"
    }
    assert batch_sources == {
        "offcn-discovery", "fenbi-discovery", "huatu-discovery",
        "gongkaoleida-discovery", "gongkaozixun-discovery",
    }
    malformed = json.loads(
        (PROJECT_ROOT / "data" / "discovery_sources.json").read_text(encoding="utf-8")
    )
    malformed["sources"][0]["publication_policy"] = "student_facing"
    with pytest.raises(ContractValidationError, match="publication_policy"):
        validate_discovery_source_registry(malformed)


def test_discovery_enrichment_stays_private_and_does_not_publish(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()

    source_record = {
        "id": "test-discovery",
        "name": "测试线索入口",
        "homepage_url": "https://discovery.example/",
        "access_mode": "public_page",
        "status": "checked",
        "scan_config": {
            "allowed_hosts": ["discovery.example"],
            "detail_fetch_limit": 1,
        },
        "publication_policy": "private_discovery_only",
    }
    lead = {
        "lead_provider": "测试线索入口",
        "discovery_source_id": "test-discovery",
        "lead_url": "https://discovery.example/notice/1",
        "title": "地质工程招聘公告",
    }

    class Scanner:
        def scan_source(self, source):
            return DiscoveryScanItem(
                source["id"], "scanned", 1, (lead,), "ok"
            )

        def enrich_lead(self, lead, *, allowed_hosts):
            assert allowed_hosts == {"discovery.example"}
            return {
                "status": "fetched",
                "source_attribution": "某市人民政府",
                "attachment_candidates": [{"url": "https://discovery.example/a.xlsx"}],
                "official_link_candidates": [
                    {"url": "https://www.example.gov.cn/notice/1", "host": "www.example.gov.cn"}
                ],
            }

    result = run_discovery_scan(
        settings,
        database,
        registry={"sources": [source_record]},
        scanner=Scanner(),
    )
    assert result["scope"] == "private_discovery_only"
    assert result["created_lead_count"] == 1
    saved = database.list_candidate_leads(limit=10)[0]
    assert saved["verification_status"] == "candidate"
    assert saved["official_domain_status"] == "unverified"
    assert saved["official_source_id"] is None
    assert saved["official_url"] is None
    assert saved["published_job_id"] is None
    assert saved["metadata"]["official_recovery"]["status"] == "fetched"
    assert result["official_url_replay"]["resolution_reasons"] == {
        "unregistered_official_candidate": 1
    }
    _, public_count = database.list_jobs(only_open=True, student_visible=True, page_size=None)
    assert public_count == 0


def test_discovery_enrichment_failure_is_private_unavailable_evidence(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    source_record = {
        "id": "test-discovery",
        "name": "测试线索入口",
        "homepage_url": "https://discovery.example/",
        "access_mode": "public_page",
        "status": "checked",
        "scan_config": {
            "allowed_hosts": ["discovery.example"],
            "detail_fetch_limit": 1,
        },
        "publication_policy": "private_discovery_only",
    }
    lead = {
        "lead_provider": "测试线索入口",
        "discovery_source_id": "test-discovery",
        "lead_url": "https://discovery.example/notice/2",
        "title": "事业单位招聘公告",
    }

    class Scanner:
        def scan_source(self, source):
            return DiscoveryScanItem(source["id"], "scanned", 1, (lead,), "ok")

        def enrich_lead(self, lead, *, allowed_hosts):
            raise DiscoveryAccessLimited("detail unavailable")

    result = run_discovery_scan(
        settings,
        database,
        registry={"sources": [source_record]},
        scanner=Scanner(),
    )
    assert result["created_lead_count"] == 1
    saved = database.list_candidate_leads(limit=10)[0]
    assert saved["verification_status"] == "candidate"
    assert saved["metadata"]["official_recovery"]["status"] == "unavailable"
    assert "detail unavailable" in saved["metadata"]["official_recovery"]["detail"]


def test_discovery_retry_never_overwrites_phase6_manual_capture(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    lead = database.create_candidate_lead(
        {
            "lead_provider": "中公教育招聘公告汇总",
            "discovery_source_id": "offcn-discovery",
            "lead_url": "https://www.offcn.com/gqzp/2026/1022/258317.html",
            "title": "中国石化校园招聘公告",
            "official_url": "https://job.sinopec.com/#/school/groupNoticeDetail?id=notice",
            "metadata": {
                "phase6_discovery_capture": {"version": 1},
                "official_recovery": {"status": "fetched", "source": "manual"},
            },
        }
    )
    source_record = {
        "id": "offcn-discovery",
        "name": "中公教育招聘公告汇总",
        "homepage_url": "https://www.offcn.com/",
        "access_mode": "public_page",
        "status": "checked",
        "scan_config": {"allowed_hosts": ["www.offcn.com"], "detail_fetch_limit": 1},
        "publication_policy": "batch_candidate_allowed",
    }

    class Scanner:
        def scan_source(self, source):
            return DiscoveryScanItem(
                source["id"],
                "scanned",
                1,
                (
                    {
                        "lead_provider": source["name"],
                        "discovery_source_id": source["id"],
                        "lead_url": lead["lead_url"],
                        "title": lead["title"],
                    },
                ),
                "ok",
            )

        def enrich_lead(self, lead, *, allowed_hosts):
            raise AssertionError("manual Phase 6 capture must not be retried")

    result = run_discovery_scan(
        settings,
        database,
        registry={"sources": [source_record]},
        scanner=Scanner(),
    )
    saved = database.get_candidate_lead(lead["id"])
    assert result["created_lead_count"] == 0
    assert saved["metadata"]["official_recovery"]["status"] == "fetched"
    assert saved["metadata"]["official_recovery"]["source"] == "manual"


def test_lead_url_normalization_and_domain_assessment() -> None:
    assert normalize_lead_url(
        "https://Example.com/jobs/1/?utm_source=wechat&from=share&x=2"
    ) == "https://example.com/jobs/1?x=2"
    records = [
        {
            "id": "official",
            "homepage_url": "https://jobs.example.gov.cn/",
            "config": {"allowed_hosts": ["example.gov.cn"]},
        }
    ]
    assert official_domain_assessment(
        "https://notice.example.gov.cn/jobs/1", records, official_source_id="official"
    )["status"] == "registered_source_match"
    assert official_domain_assessment(
        "https://notice.other.example/jobs/1", records, official_source_id="official"
    )["status"] == "source_domain_mismatch"


def test_third_party_recovery_resolves_only_registered_first_party_urls() -> None:
    recovery = {
        "official_link_candidates": [
            {
                "url": "https://jobs.example.gov.cn/notice/1",
                "label": "官方公告",
            },
            {
                "url": "https://unknown.example/jobs/1",
                "label": "招聘详情",
            },
        ]
    }
    sources = [
        {
            "id": "official",
            "homepage_url": "https://jobs.example.gov.cn/",
            "config": {"allowed_hosts": ["example.gov.cn"]},
        }
    ]
    resolved = resolve_official_url_candidates(recovery, sources)
    assert [item["url"] for item in resolved] == [
        "https://jobs.example.gov.cn/notice/1"
    ]
    assert resolved[0]["official_source_id"] == "official"


def test_third_party_recovery_keeps_unregistered_government_url_as_private_candidate() -> None:
    resolved = resolve_official_url_candidates(
        {
            "official_link_candidates": [
                {"url": "https://jobs.example.gov.cn/notice/1", "label": "官方公告"}
            ]
        },
        [],
    )
    assert resolved == [
        {
            "url": "https://jobs.example.gov.cn/notice/1",
            "label": "官方公告",
            "host": "jobs.example.gov.cn",
            "official_source_id": "",
            "domain_status": "manual_review_required",
            "candidate_quality": "job_detail_or_notice",
            "candidate_kind": "official_page",
        }
    ]


def test_persisted_recovery_demotes_an_unregistered_official_url_stage(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "lead_url": "https://clue.example/jobs/unregistered-source",
            "title": "地质工程招聘公告",
            "metadata": {
                "official_recovery": {
                    "official_link_candidates": [
                        {
                            "url": "https://www.example.gov.cn/notice/1",
                            "label": "官方公告",
                        }
                    ]
                }
            },
        }
    )
    database.update_candidate_lead(
        int(lead["id"]),
        {
            "official_url": "https://www.example.gov.cn/notice/1",
            "official_domain_status": "manual_review_required",
            "verification_status": "official_url_found",
        },
    )

    result = recover_persisted_official_url_candidates(database)
    saved = database.get_candidate_lead(int(lead["id"]))

    assert result["updated"] == 1
    assert result["errors"] == 0
    assert saved["verification_status"] == "candidate"
    assert saved["official_url"] is None
    assert saved["official_domain_status"] == "unverified"


def test_third_party_recovery_orders_job_detail_before_generic_homepage() -> None:
    resolved = resolve_official_url_candidates(
        {
            "official_link_candidates": [
                {"url": "https://jobs.example.gov.cn/", "label": "官网"},
                {"url": "https://jobs.example.gov.cn/notice/1.html", "label": "公告"},
            ]
        },
        [],
    )
    assert resolved[0]["candidate_quality"] == "job_detail_or_notice"
    assert resolved[1]["candidate_quality"] == "generic_homepage"


def test_third_party_recovery_can_resolve_registered_official_attachment_urls() -> None:
    resolved = resolve_official_url_candidates(
        {
            "attachment_candidates": [
                {"url": "https://jobs.example.gov.cn/files/positions.xlsx", "label": "岗位表"}
            ]
        },
        [
            {
                "id": "official",
                "homepage_url": "https://jobs.example.gov.cn/",
                "config": {"allowed_hosts": ["example.gov.cn"]},
            }
        ],
    )
    assert resolved[0]["candidate_kind"] == "official_attachment"


def test_official_position_tables_rank_before_application_forms() -> None:
    host = "https://rsj.example.gov.cn/module/download/downfile.jsp?filename="
    resolved = resolve_official_url_candidates(
        {
            "attachment_candidates": [
                {"url": host + "2f29.xls", "label": "报名登记表.xls"},
                {"url": host + "40ea.xls", "label": "事业单位引进人才岗位需求表.xls"},
                {"url": host + "e8f0.xlsx", "label": "企业引进人才岗位需求表.xlsx"},
            ]
        },
        [{
            "id": "lanzhou-hrss",
            "homepage_url": "https://rsj.example.gov.cn/",
            "config": {"allowed_hosts": ["rsj.example.gov.cn"]},
        }],
    )
    assert [item["url"] for item in resolved[:2]] == [
        host + "40ea.xls", host + "e8f0.xlsx"
    ]
    assert resolved[-1]["candidate_quality"] == "non_vacancy_entry"
    assert official_candidate_quality(host + "2f29.xls", label="报名登记表") == (
        "non_vacancy_entry"
    )


def test_replay_repairs_old_form_link_without_publishing(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered["id"] = "lanzhou-hrss"
    registered["homepage_url"] = "https://rsj.example.gov.cn/"
    registered["config"] = {"allowed_hosts": ["rsj.example.gov.cn"]}
    database.upsert_source(registered)
    host = "https://rsj.example.gov.cn/module/download/downfile.jsp?filename="
    form_url = host + "2f29.xls"
    table_url = host + "40ea.xls"
    lead = database.create_candidate_lead({
        "lead_provider": "行业平台",
        "discovery_source_id": "huatu-discovery",
        "lead_url": "https://clue.example/jobs/lanzhou",
        "title": "兰州市企事业单位引进人才公告",
        "metadata": {"official_recovery": {"attachment_candidates": [
            {"url": form_url, "label": "报名登记表.xls"},
            {"url": table_url, "label": "事业单位引进人才岗位需求表.xls"},
        ]}},
    })
    database.update_candidate_lead(int(lead["id"]), {
        "official_url": form_url,
        "official_source_id": registered["id"],
        "verification_status": "official_url_found",
        "verification_note": "旧分类器的私有 URL 候选",
    })
    assert discovery_funnel(
        database.list_candidate_leads(limit=5000), load_discovery_source_registry()
    )["growth_metrics"]["official_url_count"] == 0

    replay = recover_persisted_official_url_candidates(database)
    saved = database.get_candidate_lead(int(lead["id"]))
    assert replay["updated"] == 1
    assert saved["official_url"] == table_url
    assert saved["verification_status"] == "official_url_found"
    assert saved["published_job_id"] is None
    assert discovery_funnel(
        database.list_candidate_leads(limit=5000), load_discovery_source_registry()
    )["growth_metrics"]["official_url_count"] == 1


def test_persisted_recovery_replay_stops_at_official_url_stage(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered["id"] = "official-replay-source"
    registered["homepage_url"] = "https://jobs.example.gov.cn/"
    registered["config"] = {"allowed_hosts": ["example.gov.cn"]}
    database.upsert_source(registered)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://clue.example/jobs/replay",
            "title": "地质工程招聘公告",
            "metadata": {
                "official_recovery": {
                    "official_link_candidates": [
                        {
                            "url": "https://jobs.example.gov.cn/notice/1",
                            "label": "官方公告",
                        }
                    ]
                }
            },
        }
    )
    result = recover_persisted_official_url_candidates(database)
    assert result["updated"] == 1
    saved = database.get_candidate_lead(lead["id"])
    assert saved["verification_status"] == "official_url_found"
    assert saved["official_url"] == "https://jobs.example.gov.cn/notice/1"
    assert saved["official_source_id"] == "official-replay-source"
    assert saved["official_domain_status"] == "registered_source_match"
    assert saved["published_job_id"] is None
    assert saved["metadata"]["official_recovery"]["resolved_official_url_candidates"]


def test_persisted_recovery_keeps_generic_homepage_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered.update(
        {
            "id": "registered-generic-homepage",
            "homepage_url": "https://jobs.example.gov.cn/",
            "config": {"allowed_hosts": ["example.gov.cn"]},
        }
    )
    database.upsert_source(registered)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://clue.example/jobs/homepage",
            "title": "某市公务员公告线索",
            "metadata": {
                "official_recovery": {
                    "official_link_candidates": [
                        {"url": "https://jobs.example.gov.cn/", "label": "官网"}
                    ]
                }
            },
        }
    )

    result = recover_persisted_official_url_candidates(database)
    saved = database.get_candidate_lead(lead["id"])
    assert result["deferred_generic_homepage"] == 1
    assert saved["verification_status"] == "candidate"
    assert not saved["official_url"]
    assert saved["metadata"]["official_recovery"]["resolved_official_url_candidates"][0][
        "candidate_quality"
    ] == "generic_homepage"
    assert saved["metadata"]["official_recovery"]["recovery_resolution"]["reason"] == (
        "generic_homepage_only"
    )


def test_persisted_recovery_keeps_application_home_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered.update(
        {
            "id": "registered-application-homepage",
            "homepage_url": "https://bm.scs.gov.cn/",
            "config": {"allowed_hosts": ["scs.gov.cn"]},
        }
    )
    database.upsert_source(registered)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://clue.example/jobs/notice",
            "title": "某省事业单位招聘公告",
            "metadata": {
                "official_recovery": {
                    "official_link_candidates": [
                        {
                            "url": "https://bm.scs.gov.cn/pp/gkweb/core/web/ui/business/home/gkhome.html",
                            "label": "报名系统首页",
                        }
                    ]
                }
            },
        }
    )

    result = recover_persisted_official_url_candidates(database)
    saved = database.get_candidate_lead(lead["id"])
    assert result["updated"] == 0
    assert result["deferred_generic_homepage"] == 1
    assert saved["official_url"] is None
    assert saved["metadata"]["official_recovery"]["recovery_resolution"]["reason"] == (
        "non_vacancy_entry_only"
    )
    assert official_candidate_quality("https://jobs.example.gov.cn/notice/1") == (
        "job_detail_or_notice"
    )
    assert official_candidate_quality(
        "https://jobs.example.gov.cn/notice/1</a>"
    ) == "invalid_url"


def test_persisted_recovery_skips_exam_fee_lead_even_with_notice_link(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "http://www.huatu.com/z/gkksfy/",
            "title": "国家公务员考试费用",
            "metadata": {
                "official_recovery": {
                    "official_link_candidates": [
                        {"url": "https://jobs.example.gov.cn/notice/1", "label": "官方公告"}
                    ]
                }
            },
        }
    )

    result = recover_persisted_official_url_candidates(database)
    saved = database.get_candidate_lead(lead["id"])
    assert result["updated"] == 0
    assert result["skipped"] == 1
    assert saved["verification_status"] == "candidate"


def test_persisted_recovery_records_unresolved_reason_without_promotion(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://clue.example/jobs/unresolved",
            "title": "地质工程招聘公告",
            "metadata": {
                "official_recovery": {
                    "official_link_candidates": [
                        {"url": "https://unknown.example/jobs/1", "label": "详情"}
                    ]
                }
            },
        }
    )

    result = recover_persisted_official_url_candidates(database)
    saved = database.get_candidate_lead(lead["id"])
    assert result["unresolved"] == 1
    assert result["resolution_reasons"] == {
        "no_registered_or_government_candidate": 1
    }
    assert saved["verification_status"] == "candidate"
    assert saved["official_url"] is None
    resolution = saved["metadata"]["official_recovery"]["recovery_resolution"]
    assert resolution["status"] == "unresolved"
    assert resolution["reason"] == "no_registered_or_government_candidate"
    assert resolution["resolved_count"] == 0
    assert resolution["recorded_at"]


def test_persisted_candidate_recovery_retries_old_leads_without_publishing(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered.update(
        {
            "id": "official-retry-source",
            "homepage_url": "https://jobs.example.gov.cn/",
            "config": {"allowed_hosts": ["example.gov.cn"]},
        }
    )
    database.upsert_source(registered)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "discovery_source_id": "retry-discovery",
            "lead_url": "https://discovery.example/jobs/old",
            "title": "地质工程招聘",
        }
    )

    class RetryScanner:
        def enrich_lead(self, _lead, *, allowed_hosts):
            assert allowed_hosts == {"discovery.example"}
            return {
                "status": "fetched",
                "checked_at": "2026-10-11T00:00:00+08:00",
                "official_link_candidates": [
                    {"url": "https://jobs.example.gov.cn/notice/old", "label": "官方公告"}
                ],
            }

    result = enrich_persisted_candidate_leads(
        RetryScanner(),
        database,
        sources=[
            {
                "id": "retry-discovery",
                "scan_config": {"allowed_hosts": ["discovery.example"]},
            }
        ],
        source_records=database.list_sources(),
    )
    assert result["resolved"] == 1
    saved = database.get_candidate_lead(lead["id"])
    assert saved["verification_status"] == "official_url_found"
    assert saved["official_url"].endswith("/notice/old")
    assert saved["published_job_id"] is None


def test_duplicate_leads_are_idempotent_but_retain_source_mentions(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    first = database.create_candidate_lead(
        {
            "lead_provider": "华图教育",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://clue.example/jobs/1/?utm_source=wechat",
            "title": "某省地质局招聘",
            "employer_hint": "某省地质局",
        }
    )
    duplicate = database.create_candidate_lead(
        {
            "lead_provider": "中公教育",
            "discovery_source_id": "offcn-discovery",
            "lead_url": "https://clue.example/jobs/1/",
            "title": "某省地质局招聘",
            "employer_hint": "某省地质局",
        }
    )
    assert duplicate["id"] == first["id"]
    assert len(database.list_candidate_leads(limit=5000)) == 1
    mentions = database.list_candidate_lead_mentions(first["id"])
    assert {item["discovery_source_id"] for item in mentions} == {
        "huatu-discovery",
        "offcn-discovery",
    }
    funnel = discovery_funnel(
        database.list_candidate_leads(limit=5000), load_discovery_source_registry()
    )
    assert funnel["lead_total"] == 1
    assert funnel["attributed_leads"] == 1


def test_mismatched_registered_official_domain_cannot_be_verified(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered["id"] = "registered-official"
    registered["config"] = {"allowed_hosts": ["official.example.gov.cn"]}
    database.upsert_source(registered)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "lead_url": "https://clue.example/jobs/2",
            "title": "地质工程岗位",
        }
    )
    located = database.update_candidate_lead(
        lead["id"],
        {
            "official_url": "https://fake.example.com/jobs/2",
            "official_source_id": "registered-official",
            "verification_status": "official_url_found",
        },
    )
    assert located["official_domain_status"] == "source_domain_mismatch"
    with pytest.raises(ValueError, match="does not match"):
        database.update_candidate_lead(
            lead["id"],
            {
                "official_source_id": "registered-official",
                "verification_status": "official_content_verified",
                "verification_note": "尝试核验。",
            },
        )
    unscoped = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "lead_url": "https://clue.example/jobs/3",
            "title": "未登记来源的地质岗位",
        }
    )
    database.update_candidate_lead(
        unscoped["id"],
        {
            "official_url": "https://official.example.gov.cn/jobs/3",
            "verification_status": "official_url_found",
        },
    )
    with pytest.raises(ValueError, match="domain_status must match"):
        database.update_candidate_lead(
            unscoped["id"],
            {
                "official_domain_status": "registered_source_match",
                "verification_status": "official_content_verified",
                "verification_note": "不能在没有来源绑定时伪造匹配状态。",
            },
        )
    approved = database.update_candidate_lead(
        lead["id"],
        {
            "official_source_id": "registered-official",
            "official_domain_status": "manual_review_approved",
            "verification_status": "official_content_verified",
            "verification_note": "管理员已核对该政府公告主体和原文。",
        },
    )
    assert approved["official_domain_status"] == "manual_review_approved"


def test_admin_discovery_endpoints_are_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/api/admin/discovery-sources").status_code == 403
    assert client.get("/api/admin/discovery-funnel").status_code == 403
    response = client.get(
        "/api/admin/discovery-sources",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["registered_sources"] == 12
    assert payload["items"][0]["publication_policy"] == "batch_candidate_allowed"


def test_old_candidate_leads_table_gets_additive_phase5_columns(tmp_path) -> None:
    database_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE candidate_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_provider TEXT NOT NULL,
                lead_url TEXT NOT NULL,
                title TEXT NOT NULL,
                employer_hint TEXT,
                location_hint TEXT,
                province_hint TEXT,
                published_date TEXT,
                official_url TEXT,
                verification_status TEXT NOT NULL DEFAULT 'candidate',
                verification_note TEXT NOT NULL DEFAULT '',
                published_job_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO candidate_leads (
                lead_provider, lead_url, title, created_at, updated_at
            ) VALUES ('旧平台', 'https://old.example/1', '旧线索', '2026-09-21T00:00:00Z', '2026-09-21T00:00:00Z');
            """
        )
    database = Database(database_path)
    database.initialize()
    row = database.list_candidate_leads(limit=10)[0]
    assert row["lead_fingerprint"]
    assert row["official_domain_status"] == "unverified"
