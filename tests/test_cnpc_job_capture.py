from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_hub.cnpc_browser_capture import CnpcJobCaptureError, cnpc_job_capture_summary, load_cnpc_job_capture
from job_hub.cnpc_browser_runner import (
    _resolve_cdp_websocket,
    extract_cnpc_announcements,
    extract_cnpc_detail_jobs,
    write_cnpc_capture_failure,
)
from job_hub.cnpc_browser_worker import outside_maintenance_window
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


def _payload() -> dict[str, object]:
    return {
        "version": 1,
        "status": "success",
        "platform_url": "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
        "captured_at": "2026-09-26T00:00:00Z",
        "scan": {
            "pages_scanned": 13,
            "pagination_complete": True,
            "announcements_discovered": 122,
            "announcements_targeted": 1,
            "failed_announcement_details": 0,
            "jobs_discovered": 1,
            "jobs_exported": 1,
        },
        "announcements": [
            {
                "external_id": "cnpc-daqing-2026",
                "title": "大庆油田2026年秋季高校毕业生招聘启事",
                "employer": "中国石油天然气股份有限公司大庆油田分公司",
                "detail_url": "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=daqing",
                "deadline": "2026-10-15",
                "detail_status": "fields_verified",
                "observed_at": "2026-09-26T08:00:02Z",
                "reason": "官方详情页岗位表已完整读取",
            }
        ],
        "jobs": [
            {
                "external_id": "cnpc-daqing-27011103",
                "announcement_id": "cnpc-daqing-2026",
                "title": "油气田地质勘探技术支持（27011103）",
                "employer": "中国石油天然气股份有限公司大庆油田分公司",
                "detail_url": "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=daqing",
                "evidence_url": "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=daqing",
                "major": "地质资源与地质工程、地质工程、地质学",
                "degree": "硕士研究生",
                "location": "黑龙江省大庆市",
                "deadline": "2026-10-15",
                "headcount": "50",
                "observed_at": "2026-09-26T08:00:03Z",
                "field_evidence": {
                    "岗位编号": "27011103",
                    "招聘人数": "50人",
                    "官方原文": "CNPC详情页岗位表第1行",
                },
            }
        ],
    }


def _source(tmp_path: Path) -> dict[str, object]:
    return {
        "id": "cnpc-browser-test",
        "name": "CNPC 浏览器岗位测试",
        "publisher": "中国石油天然气集团有限公司",
        "homepage_url": "https://zhaopin.cnpc.com.cn/",
        "source_type": "cnpc_browser_rows",
        "category": "油气勘探开发运营与研究机构",
        "source_tier": "A",
        "enabled": True,
        "config": {
            "capture_path": "captures/cnpc-jobs.json",
            "allowed_hosts": ["zhaopin.cnpc.com.cn"],
            "application_url": "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
            "max_age_hours": 30,
            "require_complete_scan": True,
            "max_items": 20,
        },
    }


def _write_capture(tmp_path: Path) -> Path:
    path = tmp_path / "captures" / "cnpc-jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    return path


def test_cnpc_job_capture_validates_two_level_evidence(tmp_path: Path) -> None:
    path = _write_capture(tmp_path)
    payload = load_cnpc_job_capture(
        path,
        allowed_hosts=["zhaopin.cnpc.com.cn"],
        now=datetime(2026, 9, 26, 9, tzinfo=timezone.utc),
    )
    assert len(payload["announcements"]) == 1
    assert len(payload["jobs"]) == 1
    assert cnpc_job_capture_summary(payload)["publishable_job_rows"] == 1


def test_cnpc_job_capture_rejects_partial_scan(tmp_path: Path) -> None:
    payload = _payload()
    payload["scan"]["pagination_complete"] = False  # type: ignore[index]
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CnpcJobCaptureError, match="incomplete"):
        load_cnpc_job_capture(
            path,
            allowed_hosts=["zhaopin.cnpc.com.cn"],
            now=datetime(2026, 9, 26, 9, tzinfo=timezone.utc),
        )


def test_cnpc_job_capture_rejects_successful_zero_announcement_scan(tmp_path: Path) -> None:
    payload = _payload()
    payload["scan"].update(  # type: ignore[union-attr]
        {
            "announcements_discovered": 0,
            "announcements_targeted": 0,
            "jobs_discovered": 0,
            "jobs_exported": 0,
        }
    )
    payload["announcements"] = []
    payload["jobs"] = []
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CnpcJobCaptureError, match="no announcements"):
        load_cnpc_job_capture(
            path,
            allowed_hosts=["zhaopin.cnpc.com.cn"],
            now=datetime(2026, 9, 26, 9, tzinfo=timezone.utc),
        )


def test_cnpc_job_capture_rejects_unknown_announcement(tmp_path: Path) -> None:
    payload = _payload()
    payload["jobs"][0]["announcement_id"] = "missing"  # type: ignore[index]
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CnpcJobCaptureError, match="unknown announcement"):
        load_cnpc_job_capture(
            path,
            allowed_hosts=["zhaopin.cnpc.com.cn"],
            now=datetime(2026, 9, 26, 9, tzinfo=timezone.utc),
        )


def test_cnpc_job_source_converts_verified_rows(tmp_path: Path) -> None:
    _write_capture(tmp_path)
    settings = make_settings(tmp_path)
    postings = OfficialSourceCollector(settings).collect(_source(tmp_path))
    assert len(postings) == 1
    assert postings[0].external_id == "cnpc-daqing-27011103"
    assert postings[0].official_evidence_url.endswith("id=daqing")
    assert postings[0].field_evidence["招聘人数"] == "50人"


def test_cnpc_browser_runner_extracts_announcement_and_position_table() -> None:
    listing = """
    <ul class='notice-list'><li><a href='/web/recruitInfoshow.html?id=daqing'>
    大庆油田2026年秋季高校毕业生招聘启事</a> 报名截止：2026-10-15</li></ul>
    """
    detail = """
    <table><tr><th>岗位名称</th><th>专业要求</th><th>学历要求</th><th>工作地点</th><th>招聘人数</th><th>报名截止</th></tr>
    <tr><td>油气田地质勘探技术支持（27011103）</td><td>地质资源与地质工程、地质学</td>
    <td>硕士研究生</td><td>黑龙江省大庆市</td><td>50人</td><td>2026-10-15</td></tr></table>
    """
    hosts = {"zhaopin.cnpc.com.cn"}
    announcements = extract_cnpc_announcements(
        listing,
        page_url="https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
        allowed_hosts=hosts,
        config={"default_employer": "中国石油天然气股份有限公司大庆油田分公司"},
    )
    assert len(announcements) == 1
    jobs = extract_cnpc_detail_jobs(
        detail,
        detail_url=announcements[0]["detail_url"],
        announcement=announcements[0],
        allowed_hosts=hosts,
        config={},
    )
    assert jobs[0]["external_id"].endswith(":27011103")
    assert jobs[0]["field_evidence"]["招聘人数"] == "50人"


def test_cnpc_browser_worker_respects_maintenance_window() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    assert outside_maintenance_window(datetime(2026, 9, 26, 5, 59, tzinfo=tz)) is False
    assert outside_maintenance_window(datetime(2026, 9, 26, 6, 5, tzinfo=tz)) is True
    assert outside_maintenance_window(datetime(2026, 9, 26, 23, 49, tzinfo=tz)) is True
    assert outside_maintenance_window(datetime(2026, 9, 27, 0, 0, tzinfo=tz)) is False


def test_cdp_endpoint_rewrites_headless_shell_websocket_host(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"webSocketDebuggerUrl": "ws://localhost/devtools/browser/abc"}

    def fake_get(_url: str, *, headers: dict[str, str], timeout: int) -> Response:
        assert headers == {"Host": "localhost"}
        assert timeout == 10
        return Response()

    monkeypatch.setattr("job_hub.cnpc_browser_runner.requests.get", fake_get)
    monkeypatch.setattr("job_hub.cnpc_browser_runner.socket.gethostbyname", lambda _host: "172.18.0.3")
    assert _resolve_cdp_websocket("http://headless-shell:9222") == (
        "ws://172.18.0.3:9222/devtools/browser/abc"
    )


def test_cnpc_failure_capture_replaces_stale_success_artifact(tmp_path: Path) -> None:
    path = tmp_path / "captures" / "cnpc-jobs.json"
    write_cnpc_capture_failure(
        output=path,
        platform_url="https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
        status="access_limited",
        reason="HTTP 412",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "access_limited"
    assert payload["scan"]["failure_reason"] == "HTTP 412"
