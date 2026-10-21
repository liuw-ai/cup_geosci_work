"""Audit the retained third-party table trial without touching the database.

The Huatu Gansu attachment was successfully captured on 2026-10-18, but the
same asset host was later disallowed by robots.txt.  This report keeps those
facts separate: a retained historical file can be audited locally, while a
current network recheck must not be interpreted as permission to download it
again.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.government_table_parser import load_position_table_with_report


TRIAL = ROOT / "docs" / "current" / "phase4-third-party-trial-2026-10-18.json"
TABLE_DISCOVERY = ROOT / "docs" / "current" / "phase4-third-party-huatu-table-discovery-2026-10-19.json"
OUTPUT = ROOT / "docs" / "current" / "phase4-third-party-table-trial-reconciliation-2026-10-21.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_current_recheck(table_url: str) -> dict[str, object]:
    payload = json.loads(TABLE_DISCOVERY.read_text(encoding="utf-8"))
    for item in payload.get("items", []):
        for link in item.get("table_links", []):
            if link.get("table_url") == table_url:
                return {
                    "discovery_page_url": item.get("discovery_page_url"),
                    "robots": link.get("table_robots", {}),
                    "download_decision": link.get("download_decision"),
                }
    raise ValueError(f"current table discovery report has no record for {table_url}")


def build_report() -> dict[str, object]:
    trial = json.loads(TRIAL.read_text(encoding="utf-8"))
    capture = trial["third_party_table_capture"]
    funnel = trial["funnel"]
    retained_path = ROOT / str(capture["retained_path"])
    if not retained_path.is_file():
        raise FileNotFoundError(retained_path)

    actual_sha256 = _sha256(retained_path)
    parsed = load_position_table_with_report(retained_path)
    parsed_rows = len(parsed.get("rows", []))
    expected_rows = int(funnel["input_rows"])
    if parsed_rows != expected_rows:
        raise ValueError(f"retained table row count mismatch: expected {expected_rows}, got {parsed_rows}")

    current_recheck = _find_current_recheck(str(trial["third_party_table_url"]))
    robots = current_recheck.get("robots", {})
    return {
        "phase": "phase4",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "historical_capture_retained_current_access_blocked",
        "policy": {
            "network_recheck_is_read_only": True,
            "robots_denial_is_not_source_absence": True,
            "third_party_table_is_candidate_evidence_only": True,
            "official_evidence_required_before_publication": True,
        },
        "source": {
            "source_id": trial["source_id"],
            "source_grade": trial["source_grade"],
            "discovery_page_url": trial["discovery_page_url"],
            "third_party_table_url": trial["third_party_table_url"],
        },
        "historical_capture": {
            "captured_at": capture["captured_at"],
            "http_status": capture["http_status"],
            "bytes": capture["bytes"],
            "expected_sha256": capture["sha256"],
            "actual_sha256": actual_sha256,
            "sha256_matches": actual_sha256 == capture["sha256"],
            "retained_path": capture["retained_path"],
            "file_exists": True,
            "parsed_input_rows": parsed_rows,
        },
        "historical_funnel": {
            "input_rows": funnel["input_rows"],
            "accepted_rows": funnel["accepted_rows"],
            "rejected_rows": funnel["rejected_rows"],
            "isolated_candidate_leads": funnel["isolated_candidate_leads"],
            "official_recovery_bound": funnel["official_recovery_bound"],
            "formal_jobs_created": funnel["formal_jobs_created"],
            "student_visible_changes": funnel["student_visible_changes"],
            "professional_screening": trial["screening"]["professional_result"],
        },
        "current_network_recheck": {
            "discovery_page_url": current_recheck["discovery_page_url"],
            "robots_url": robots.get("robots_url"),
            "robots_status": robots.get("status"),
            "robots_allowed": robots.get("allowed"),
            "download_decision": current_recheck["download_decision"],
            "downloaded": False,
            "reason": "当前 robots.txt 禁止附件抓取；仅使用已留存文件完成本地审计，不重复下载。",
        },
        "publication_boundary": {
            "formal_database_written": trial["policy_checks"]["formal_database_written"],
            "third_party_link_as_application": trial["policy_checks"]["third_party_link_as_application"],
            "robots_bypass": trial["policy_checks"]["robots_bypass"],
        },
    }


def main() -> None:
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "status": report["status"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
