from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import capture_phase4_huatu_cnpc_chuanqing as capture


def _page() -> bytes:
    positions = "".join(
        f"<p>{index}. 岩石与地质工程专家{index}</p>" for index in range(1, 13)
    )
    html = f"""
    <html><head><meta charset='GBK'><title>川庆钻探招聘</title></head>
    <body>{positions}
    <p>首席专家岗位：原则上年龄不超过55周岁，具有博士学位。</p>
    <p>报名时间：即日起至岗位招满即止。</p>
    <p>liuh520_jx@cnpc.com.cn</p></body></html>
    """
    return html.encode("gbk")


def test_parse_page_keeps_all_twelve_rows() -> None:
    parsed = capture.parse_page(_page())

    assert len(parsed["positions"]) == 12
    assert parsed["positions"][0]["row_number"] == 1
    assert parsed["positions"][-1]["row_number"] == 12
    assert parsed["application_email"] == "liuh520_jx@cnpc.com.cn"
    assert parsed["official_url_status"] == "not_found"


def test_capture_registers_each_row_as_private_candidate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capture, "check_robots", lambda _url: {"robots_allowed": True, "decision": "permitted"})
    monkeypatch.setattr(
        capture,
        "_read",
        lambda _url: (_page(), {"status": "200", "content_type": "text/html"}),
    )

    output = tmp_path / "evidence.json"
    payload = capture.build_payload(output, tmp_path / "raw")
    database_path = tmp_path / "isolated.sqlite3"
    rows = capture.register_candidates(database_path, payload)

    assert len(rows) == 12
    connection = sqlite3.connect(database_path)
    assert connection.execute(
        "select count(*) from candidate_leads where verification_status = 'candidate'"
    ).fetchone()[0] == 12
    assert connection.execute(
        "select count(*) from candidate_leads where official_url is not null"
    ).fetchone()[0] == 0
    assert connection.execute(
        "select count(distinct lead_fingerprint) from candidate_leads"
    ).fetchone()[0] == 12
