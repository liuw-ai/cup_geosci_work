from job_hub.capture_evidence import attach_capture_manifest, validate_capture_manifest


def test_capture_manifest_is_deterministic_and_tracks_failures() -> None:
    payload = {
        "status": "partial",
        "captured_at": "2026-10-02T00:00:00Z",
        "scan": {"pages_scanned": 2, "candidate_rows": 4, "detail_discovered": 4, "detail_succeeded": 3, "detail_failed": 1},
        "failure_records": [{"detail_url": "https://official.example/job/4"}],
        "rows": [{"external_id": "1"}],
    }
    first = attach_capture_manifest(payload, source_id="source-a", adapter_version="v1")
    second = attach_capture_manifest(payload, source_id="source-a", adapter_version="v1")
    assert first["capture_evidence"] == second["capture_evidence"]
    assert first["capture_evidence"]["failure_count"] == 1
    assert first["capture_evidence"]["failure_locators"] == ["https://official.example/job/4"]
    assert validate_capture_manifest(first)["source_id"] == "source-a"


def test_capture_manifest_hash_changes_when_capture_content_changes() -> None:
    payload = {"status": "success", "captured_at": "2026-10-02T00:00:00Z", "rows": []}
    first = attach_capture_manifest(payload, source_id="source-a", adapter_version="v1")
    changed = attach_capture_manifest({**payload, "rows": [{"external_id": "2"}]}, source_id="source-a", adapter_version="v1")
    assert first["capture_evidence"]["payload_sha256"] != changed["capture_evidence"]["payload_sha256"]
