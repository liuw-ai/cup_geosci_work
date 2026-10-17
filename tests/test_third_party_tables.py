import pytest

from job_hub.third_party_tables import discover_table_links, table_candidates


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
