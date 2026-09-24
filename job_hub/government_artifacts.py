"""Versioned manifest for official provincial position-table artifacts.

The manifest is intentionally separate from ``sources.json``.  A source is
an entry point; an artifact is one specific notice attachment with its own
deadline and server-download state.  Registering an artifact never downloads
it or publishes rows.  The existing controlled attachment processor performs
the download, hash, extraction and review steps afterwards.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "government_artifact_manifest.json"
ARTIFACT_STATUSES = frozenset(
    {"server_download_pending", "historical_closed", "source_unavailable"}
)


class GovernmentArtifactContractError(ValueError):
    """Raised when an official artifact manifest is incomplete."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise GovernmentArtifactContractError(f"{field} must be non-empty")
    return result


def _url(value: Any, field: str) -> str:
    result = _text(value, field)
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GovernmentArtifactContractError(f"{field} must be an HTTP(S) URL")
    return result


def _date(value: Any, field: str) -> str:
    result = _text(value, field)
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise GovernmentArtifactContractError(f"{field} must use YYYY-MM-DD") from error
    return result


def load_government_artifact_manifest(
    path: Path | str | None = None,
) -> dict[str, Any]:
    manifest_path = Path(path or DEFAULT_MANIFEST_PATH)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GovernmentArtifactContractError(f"Cannot read {manifest_path}") from error
    if not isinstance(payload, dict):
        raise GovernmentArtifactContractError("government artifact manifest must be an object")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise GovernmentArtifactContractError("manifest version must be a positive integer")
    as_of = _date(payload.get("as_of"), "manifest.as_of")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise GovernmentArtifactContractError("manifest.artifacts must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = (
        "id",
        "source_id",
        "province",
        "position_type",
        "notice_url",
        "attachment_url",
        "artifact_kind",
        "deadline_date",
        "observed_on",
        "status",
    )
    for index, value in enumerate(artifacts):
        context = f"artifacts[{index}]"
        if not isinstance(value, dict):
            raise GovernmentArtifactContractError(f"{context} must be an object")
        missing = [field for field in required if field not in value]
        if missing:
            raise GovernmentArtifactContractError(f"{context} is missing: {', '.join(missing)}")
        item = dict(value)
        for field in ("id", "source_id", "province", "position_type", "artifact_kind"):
            item[field] = _text(item[field], f"{context}.{field}")
        item["notice_url"] = _url(item["notice_url"], f"{context}.notice_url")
        item["attachment_url"] = _url(item["attachment_url"], f"{context}.attachment_url")
        item["deadline_date"] = _date(item["deadline_date"], f"{context}.deadline_date")
        item["observed_on"] = _date(item["observed_on"], f"{context}.observed_on")
        item["status"] = _text(item["status"], f"{context}.status")
        if item["status"] not in ARTIFACT_STATUSES:
            raise GovernmentArtifactContractError(f"{context}.status is unsupported")
        if item["id"] in seen:
            raise GovernmentArtifactContractError(f"duplicate artifact id: {item['id']}")
        seen.add(item["id"])
        item["note"] = str(item.get("note") or "").strip()
        normalized.append(item)
    return {
        "version": version,
        "as_of": as_of,
        "description": str(payload.get("description") or "").strip(),
        "artifacts": normalized,
    }


def register_government_artifacts(database: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Register manifest rows in the private attachment ledger only."""
    registered: list[dict[str, Any]] = []
    for item in manifest["artifacts"]:
        registered.append(
            database.upsert_source_artifact(
                {
                    "source_id": item["source_id"],
                    "parent_url": item["notice_url"],
                    "artifact_url": item["attachment_url"],
                    "artifact_kind": item["artifact_kind"],
                    "media_type": None,
                    "extraction_status": "registered",
                    "metadata": {
                        "government_artifact_id": item["id"],
                        "position_type": item["position_type"],
                        "province": item["province"],
                        "deadline_date": item["deadline_date"],
                        "observed_on": item["observed_on"],
                        "manifest_status": item["status"],
                        "note": item["note"],
                    },
                }
            )
        )
    return registered

