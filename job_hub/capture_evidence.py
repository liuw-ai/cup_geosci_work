"""Evidence manifest helpers for browser and attachment captures."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any


def attach_capture_manifest(
    payload: dict[str, Any],
    *,
    source_id: str,
    adapter_version: str,
) -> dict[str, Any]:
    """Attach a deterministic, non-public manifest to a capture payload.

    The hash covers the capture content before the manifest is added. This makes
    a saved capture auditable without exposing raw request bodies in reports.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    captured_at = str(payload.get("captured_at") or "").strip()
    if not captured_at:
        captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    capture_id = hashlib.sha256(
        f"{source_id}|{captured_at}|{payload_hash}".encode("utf-8")
    ).hexdigest()[:24]
    scan = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
    failures = payload.get("failure_records")
    if not isinstance(failures, list):
        failures = scan.get("failure_records") if isinstance(scan.get("failure_records"), list) else []
    rejected = payload.get("rejected_records")
    if not isinstance(rejected, list):
        rejected = scan.get("rejected_records") if isinstance(scan.get("rejected_records"), list) else []
    manifest = {
        "capture_id": capture_id,
        "source_id": str(source_id),
        "adapter_version": str(adapter_version),
        "captured_at": captured_at,
        "payload_sha256": payload_hash,
        "hash_scope": "canonical_capture_payload_without_manifest",
        "pages_scanned": _int_or_none(scan.get("pages_scanned")),
        "units_scanned": _int_or_none(scan.get("units_scanned")),
        "candidate_count": _int_or_none(scan.get("candidate_rows")),
        "detail_discovered": _int_or_none(scan.get("detail_discovered")),
        "detail_succeeded": _int_or_none(scan.get("detail_succeeded")),
        "detail_failed": _int_or_none(scan.get("detail_failed")),
        "failure_count": len(failures),
        "rejected_count": len(rejected),
        "failure_locators": [
            str(item.get("detail_url") or item.get("unit") or item.get("page") or "")
            for item in failures
            if isinstance(item, dict)
        ][:100],
    }
    result = dict(payload)
    result["capture_evidence"] = manifest
    return result


def ensure_capture_manifest(
    payload: dict[str, Any],
    *,
    source_id: str,
    adapter_version: str,
) -> dict[str, Any]:
    """Attach a manifest exactly once at a capture persistence boundary.

    Browser adapters may pass through more than one persistence helper (for
    example a CMGB partial capture can be archived and copied to a sidecar).
    Re-hashing an already manifested payload would make the evidence hash
    depend on the persistence path, so existing valid manifests are preserved.
    A malformed pre-existing value is rejected instead of silently replacing
    evidence produced by another adapter.
    """

    existing = payload.get("capture_evidence")
    if existing is not None:
        if not isinstance(existing, dict):
            raise ValueError("capture_evidence must be an object")
        manifest = validate_capture_manifest(payload)
        if str(manifest.get("source_id")) != str(source_id):
            raise ValueError("capture_evidence source_id does not match persistence source")
        if str(manifest.get("adapter_version")) != str(adapter_version):
            raise ValueError(
                "capture_evidence adapter_version does not match persistence adapter"
            )
        return payload
    return attach_capture_manifest(
        payload,
        source_id=source_id,
        adapter_version=adapter_version,
    )


def validate_capture_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the presence and shape of a capture manifest."""
    manifest = payload.get("capture_evidence")
    if not isinstance(manifest, dict):
        raise ValueError("capture_evidence manifest is required")
    required = ("capture_id", "source_id", "adapter_version", "payload_sha256", "hash_scope")
    missing = [field for field in required if not str(manifest.get(field) or "").strip()]
    if missing:
        raise ValueError(f"capture_evidence missing: {', '.join(missing)}")
    return manifest


def _int_or_none(value: Any) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
