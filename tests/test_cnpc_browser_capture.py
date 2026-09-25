from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_hub.cnpc_browser_capture import (
    CnpcBrowserCaptureError,
    build_cnpc_browser_capture_report,
    load_cnpc_browser_capture,
)


ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "data" / "verified" / "cnpc-browser-index-20260925.json"


def test_cnpc_browser_capture_records_full_index_and_degraded_details() -> None:
    payload = load_cnpc_browser_capture(CAPTURE)
    report = build_cnpc_browser_capture_report(CAPTURE)
    assert payload["scan"]["pages_scanned"] == 13
    assert payload["scan"]["announcements_discovered"] == 122
    assert payload["scan"]["pagination_complete"] is True
    assert len(payload["detail_targets"]) == 3
    assert report["summary"]["publishable_job_rows"] == 0
    assert report["summary"]["detail_status"] == {"official_detail_api_degraded": 3}


def test_cnpc_browser_capture_rejects_non_official_target(tmp_path: Path) -> None:
    target = tmp_path / "capture.json"
    payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
    payload["detail_targets"][0]["detail_url"] = "https://example.com/not-cnpc"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CnpcBrowserCaptureError, match="official CNPC host"):
        load_cnpc_browser_capture(target)


def test_cnpc_browser_capture_rejects_target_count_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "capture.json"
    payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
    payload["scan"]["announcements_targeted"] = 2
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CnpcBrowserCaptureError, match="does not match"):
        load_cnpc_browser_capture(target)


def test_cnpc_browser_capture_requires_timezone(tmp_path: Path) -> None:
    target = tmp_path / "capture.json"
    payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
    payload["captured_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CnpcBrowserCaptureError, match="timezone"):
        load_cnpc_browser_capture(target)
