"""Auditable observations from national-energy public endpoint probes."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from job_hub.contracts import ContractValidationError, validate_national_source_probes
from job_hub.sources import load_source_registries


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROBES_PATH = PROJECT_ROOT / "data" / "national_source_probes.json"
SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "provincial_sources.json"


def _registered_source_ids() -> set[str]:
    ids: set[str] = set()
    for path in (SOURCE_REGISTRY_PATH, PROVINCIAL_SOURCE_REGISTRY_PATH):
        if path.exists():
            ids.update(
                str(item["id"])
                for item in load_source_registries([str(path)])
                if item.get("id")
            )
    return ids


def load_national_source_probes(
    path: Path | None = None,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    probe_path = path or PROBES_PATH
    try:
        payload = json.loads(probe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read national source probes: {probe_path}") from error
    try:
        return validate_national_source_probes(
            payload,
            source_ids=_registered_source_ids() if source_ids is None else source_ids,
        )
    except ContractValidationError as error:
        raise ValueError(f"national_source_probes.json is invalid: {error}") from error


def national_source_probe_summary(
    probes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = probes or load_national_source_probes()
    rows = payload["probes"]
    results = Counter(str(row["result"]) for row in rows)
    conclusions = Counter(str(row["scan_conclusion"]) for row in rows)
    return {
        "as_of": payload["as_of"],
        "probe_count": len(rows),
        "result_counts": dict(sorted(results.items())),
        "scan_conclusion_counts": dict(sorted(conclusions.items())),
        "published_jobs": sum(int(row["published_jobs"]) for row in rows),
        "scope_note": (
            "探测证据只记录入口/API行为；业务错误、访问故障或适配器失败不能解释为无岗位。"
        ),
    }
