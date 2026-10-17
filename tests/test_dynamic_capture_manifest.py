from __future__ import annotations

import json
from pathlib import Path

from job_hub.browser_capture import write_browser_capture_failure
from job_hub.browser_capture import BrowserCaptureError, load_browser_capture
from job_hub.capture_evidence import validate_capture_manifest
from job_hub.cmgb_browser_capture import write_cmgb_capture_failure
from job_hub.cnpc_browser_runner import write_cnpc_capture_failure
from job_hub.cnooc_browser_capture import write_cnooc_capture_failure
from job_hub.sinopec_browser_capture import write_sinopec_capture_failure


def _manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_capture_manifest(payload)


def test_all_dynamic_failure_sidecars_carry_a_provenance_manifest(tmp_path: Path) -> None:
    generic = tmp_path / "generic.json"
    write_browser_capture_failure(
        output=generic,
        platform_url="https://official.example/jobs",
        status="access_limited",
        reason="robots unavailable",
        source_id="generic-source",
        adapter_version="generic-v2",
    )
    assert _manifest(generic.with_name("generic.json.failure.json"))["source_id"] == "generic-source"

    cnpc = tmp_path / "cnpc.json"
    write_cnpc_capture_failure(
        output=cnpc,
        platform_url="https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
        status="parse_failed",
        reason="detail table missing",
    )
    assert _manifest(cnpc.with_suffix(".failure.json"))["source_id"] == "cnpc-career-browser"

    cmgb = tmp_path / "cmgb.json"
    write_cmgb_capture_failure(
        output=cmgb,
        platform_url="https://cmgb.iguopin.com/jobCampus",
        status="partial",
        reason="one detail timed out",
    )
    cmgb_payload = json.loads((cmgb.parent / "cmgb.failures" / next(iter(
        p.name for p in (cmgb.parent / "cmgb.failures").iterdir()
    ))).read_text(encoding="utf-8"))
    assert validate_capture_manifest(cmgb_payload)["source_id"] == "cmgb-iguopin-browser"

    sinopec = tmp_path / "sinopec.json"
    write_sinopec_capture_failure(
        output=sinopec,
        platform_url="https://job.sinopec.com/",
        status="parse_failed",
        reason="SPA response changed",
    )
    assert _manifest(sinopec.with_name("sinopec.json.failure.json"))["source_id"] == "sinopec-career"

    cnooc = tmp_path / "cnooc.json"
    write_cnooc_capture_failure(
        output=cnooc,
        platform_url="https://cnooc.zhaopin.com/",
        status="access_limited",
        reason="detail challenge",
    )
    assert _manifest(cnooc.with_name("cnooc.json.failure.json"))["source_id"] == "cnooc-career-browser"


def test_production_manifest_gate_rejects_legacy_generic_capture() -> None:
    capture = Path(__file__).parent / "fixtures" / "browser" / "pipechina_capture.json"
    try:
        load_browser_capture(
            capture,
            allowed_hosts=["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
            require_capture_manifest=True,
        )
    except BrowserCaptureError as error:
        assert "capture_evidence" in str(error)
    else:  # pragma: no cover - the fixture intentionally predates manifests
        raise AssertionError("legacy capture unexpectedly passed production manifest gate")


def test_registered_browser_sources_require_capture_manifests() -> None:
    registry = json.loads(
        (Path(__file__).parent.parent / "data" / "sources.json").read_text(encoding="utf-8")
    )
    browser_types = {
        "official_browser_rows",
        "cnpc_browser_rows",
        "cmgb_browser_rows",
        "cnooc_browser_rows",
        "sinopec_spa_rows",
    }
    rows = [item for item in registry if item.get("source_type") in browser_types]
    assert rows
    assert all(item.get("config", {}).get("require_capture_manifest") is True for item in rows)
