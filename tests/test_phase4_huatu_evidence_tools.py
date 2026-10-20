from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_huatu_page_evidence import analyze_page


def test_huatu_analyzer_decodes_declared_gb2312_and_keeps_raw_hash(tmp_path: Path) -> None:
    html = """<html><head><meta charset='gb2312'><title>页面标题</title></head>
    <body><h1>2026年地质招聘</h1><div class='artBcon'><p>原文来源：https://example.gov.cn/notice/1</p>
    <a href='https://example.gov.cn/table.xlsx'>岗位表</a></div></body></html>""".encode("gb2312")
    page = tmp_path / "page-01.html"
    page.write_bytes(html)

    result = analyze_page(page)

    assert result["declared_charset"].lower() == "gb2312"
    assert result["title"] == "2026年地质招聘"
    assert result["source_urls"] == ["https://example.gov.cn/notice/1"]
    assert result["attachment_links"][0]["url"].endswith("table.xlsx")
    assert len(result["article_text_sha256"]) == 64


def test_huatu_report_keeps_official_recovery_gate_explicit() -> None:
    report = json.loads(
        Path("docs/current/phase4-third-party-huatu-official-recovery-2026-10-21.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["policy"]["official_url_required_before_formal_job"] is True
    assert report["outcome"]["formal_jobs_created"] == 0
    assert any(row["decision"] == "no_geoscience_row_published" for row in report["rows"])
    assert any(row["decision"] == "rejected_out_of_scope" for row in report["rows"])
