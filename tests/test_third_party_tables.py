import hashlib
from pathlib import Path

import pytest

from job_hub import third_party_tables
from job_hub.third_party_tables import (
    discover_table_links,
    fetch_and_normalize_third_party_table,
    table_candidates,
)


def test_third_party_table_rows_become_private_candidates_with_provenance():
    candidates = table_candidates(
        {
            "batch": {
                "id": "offcn-2027",
                "province": "北京",
                "rows": [["A01", "自然资源局", "地质管理", "地质学", "硕士", 1, "北京岗位!4", "北京"]],
            },
            "download": {
                "sha256": "abc123",
                "bytes": 128,
                "content_type": "application/vnd.ms-excel",
                "captured_at": "2026-10-17T00:00:00Z",
                "retained_path": "runtime/third-party/offcn.xlsx",
            },
        },
        source_id="offcn-discovery",
        third_party_table_url="https://www.offcn.com/table.xlsx",
        official_notice_url="https://gov.example/notice",
        official_application_url="https://gov.example/apply",
        discovery_page_url="https://www.offcn.com/guide/",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["official_url"] == "https://gov.example/notice"
    assert candidate["third_party_table_url"].endswith("table.xlsx")
    assert candidate["metadata"]["publication_status"] == "candidate_only"
    assert candidate["lead_url"].endswith("#row-北京岗位!4")
    assert candidate["metadata"]["application_url"] == "https://gov.example/apply"
    assert candidate["metadata"]["table_evidence"]["sha256"] == "abc123"
    assert candidate["metadata"]["table_evidence"]["discovery_page_url"] == "https://www.offcn.com/guide/"


def test_table_discovery_rejects_page_outside_registered_source_host():
    with pytest.raises(ValueError, match="discovery page host"):
        discover_table_links(
            '<a href="https://www.offcn.com/table.xlsx">职位表</a>',
            page_url="https://untrusted.example/guide",
            source_id="offcn-discovery",
        )


def test_fetch_retains_official_table_evidence_and_hash(monkeypatch, tmp_path):
    from openpyxl import Workbook

    fixture = tmp_path / "fixture.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "内蒙古岗位"
    sheet.append(["岗位代码", "用人单位", "岗位名称", "专业要求", "学历要求", "招录人数", "工作地点"])
    sheet.append(["A01", "自然资源局", "地质管理", "地质学", "硕士", 1, "呼和浩特"])
    workbook.save(fixture)
    body = fixture.read_bytes()
    monkeypatch.setattr(
        third_party_tables,
        "_bounded_download",
        lambda url, timeout, max_bytes: (
            body,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    )

    payload = fetch_and_normalize_third_party_table(
        source_id="offcn-discovery",
        third_party_table_url="https://www.offcn.com/2026/positions.xlsx",
        batch_id="offcn-test-2026",
        position_type="civil_service",
        province="内蒙古",
        official_notice_url="https://www.gov.cn/notice/2026",
        official_table_url="https://www.gov.cn/notice/2026/positions.xlsx",
        discovery_page_url="https://www.offcn.com/guide/2026",
        storage_dir=tmp_path,
    )

    assert payload["provenance"]["official_table_url"] == (
        "https://www.gov.cn/notice/2026/positions.xlsx"
    )
    assert payload["download"]["sha256"] == hashlib.sha256(body).hexdigest()
    retained = Path(payload["download"]["retained_path"])
    assert retained.is_file()
    assert retained.read_bytes() == body
    assert payload["candidates"]
    assert payload["candidates"][0]["official_table_url"] == (
        "https://www.gov.cn/notice/2026/positions.xlsx"
    )
