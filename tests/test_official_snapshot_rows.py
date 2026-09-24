import json
from pathlib import Path

import pytest

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import OfficialSourceCollector, SourceCollectionError

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


SNAPSHOT_SOURCE = {
    "id": "pipechina-career",
    "name": "国家管网集团招聘平台官方快照",
    "publisher": "国家石油天然气管网集团有限公司",
    "homepage_url": "https://zhaopin.pipechina.com.cn/",
    "source_type": "official_snapshot_rows",
    "category": "管网、炼化与综合能源",
    "source_tier": "A",
    "config": {
        "snapshot_path": "data/verified/pipechina-2027-geoscience-20260924.json",
        "official_evidence_url": "https://www.pipechina.com.cn/front/tzgg/3004720.html",
        "application_url": "https://zhaopin.pipechina.com.cn/recruit",
        "allowed_hosts": ["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
    },
}


def test_official_snapshot_rows_loads_all_verified_rows(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))

    postings = collector.collect(SNAPSHOT_SOURCE)

    assert len(postings) == 24
    assert postings[0].field_evidence["岗位"] == postings[0].title
    assert postings[0].official_evidence_url.startswith(
        "https://www.pipechina.com.cn/"
    )
    assert any(posting.title == "地质探测" for posting in postings)
    assert any(posting.title == "管道工程师（甘肃兰州）" for posting in postings)


def test_pipechina_snapshot_builds_job_detail_links_and_search_fallback(tmp_path) -> None:
    registry = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )
    source = next(item for item in registry if item["id"] == "pipechina-career")
    postings = OfficialSourceCollector(make_settings(tmp_path)).collect(source)

    geology = next(posting for posting in postings if posting.title == "地质探测")
    assert geology.field_evidence["官方岗位编号"] == "54647"
    assert geology.field_evidence["招聘人数"] == "3"
    assert "common_board_view_anonymous" in geology.source_url
    assert "_HB4_eyJpZCI6NTQ2NDcs" in geology.source_url
    assert "search_key=%E5%9C%B0%E8%B4%A8" in geology.field_evidence["官方地质筛选入口"]
    assert geology.field_evidence["官方详情链接"] == geology.source_url


def test_official_snapshot_rows_preserves_non_matching_rows_for_gate(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))

    postings = collector.collect(SNAPSHOT_SOURCE)

    non_target = next(
        posting for posting in postings if posting.title == "管道工程师（南昌）"
    )
    assert "地质学" not in non_target.field_evidence["专业范围"]
    assert "地质工程" not in non_target.field_evidence["专业范围"]


def test_cupb_snapshot_rows_pass_the_same_publication_gate(tmp_path) -> None:
    source = {
        "id": "cupb-verified-geoscience-snapshot",
        "name": "CUPB地学岗位快照",
        "publisher": "中国石油大学（北京）就业指导中心",
        "homepage_url": "https://career.cup.edu.cn/campus",
        "source_type": "official_snapshot_rows",
        "category": "科研院所、高校与博士后",
        "source_tier": "B",
        "config": {
            "snapshot_path": "data/verified/cupb-geoscience-20260924.json",
            "official_evidence_url": "https://career.cup.edu.cn/campus/view/id/460426",
            "application_url": "https://career.cup.edu.cn/campus",
            "allowed_hosts": [
                "career.cup.edu.cn",
                "www.glut.edu.cn",
                "www.jxust.edu.cn",
                "zhaopin.chnenergy.com.cn",
            ],
        },
    }
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    postings = OfficialSourceCollector(settings).collect(source)

    normalized = [pipeline.normalize_posting(posting, source) for posting in postings]
    statuses = {item["external_id"]: item["publication_status"] for item in normalized}

    assert statuses["cupb-460426-geoscience-faculty"] == "student_eligible"
    assert statuses["cupb-460487-postdoc-mineral-green-mining"] == "student_eligible"
    assert statuses["cupb-460487-postdoc-geohazard"] == "student_eligible"
    assert statuses["cupb-460520-national-energy-unrestricted"] == "unrestricted_eligible"


def test_cnpc_snapshot_exposes_opaque_detail_id_and_degraded_page_state(tmp_path) -> None:
    registry = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )
    source = next(item for item in registry if item["id"] == "cnpc-career")
    collector = OfficialSourceCollector(make_settings(tmp_path))

    posting = collector.collect(source)[0]

    assert posting.field_evidence["官方详情编号"].startswith("8b8b6c9e")
    assert posting.field_evidence["官方详情状态"] == "official_detail_api_degraded"
    assert posting.field_evidence["官方招聘入口"] == (
        "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html"
    )


def test_official_snapshot_rows_reject_unallowlisted_evidence_hosts(tmp_path) -> None:
    source = {
        **SNAPSHOT_SOURCE,
        "config": {
            **SNAPSHOT_SOURCE["config"],
            "allowed_hosts": ["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
        },
    }
    with pytest.raises(SourceCollectionError, match="host is not allowlisted"):
        OfficialSourceCollector(make_settings(tmp_path)).collect(
            {
                **source,
                "config": {
                    **source["config"],
                    "snapshot_path": "data/verified/cupb-geoscience-20260924.json",
                },
            }
        )
