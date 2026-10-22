from __future__ import annotations

from pathlib import Path

from job_hub.db import Database
from scripts.build_phase6_local_review import _read_database, _review_id


def test_phase6_review_id_is_derived_from_the_review_workspace() -> None:
    assert _review_id(Path("review-2026-10-23-r3")) == (
        "phase6-local-review-2026-10-23-r3"
    )


def test_phase6_review_report_lists_private_notice_captures_only(tmp_path) -> None:
    database_path = tmp_path / "review.sqlite3"
    database = Database(database_path)
    database.initialize()
    database.create_candidate_lead(
        {
            "lead_provider": "中公教育招聘公告汇总",
            "lead_url": "https://www.offcn.com/gqzp/2026/notice.html",
            "title": "中国石化校园招聘公告",
            "metadata": {
                "phase6_discovery_capture": {
                    "capture_kind": "third_party_notice",
                    "publication_gate": "official_content_verified_required",
                }
            },
        }
    )

    report = _read_database(database_path)

    assert report["counts"]["candidate_leads"] == 1
    assert len(report["third_party_notice_captures"]) == 1
    capture = report["third_party_notice_captures"][0]
    assert capture["title"] == "中国石化校园招聘公告"
    assert capture["verification_status"] == "candidate"
    assert capture["capture"]["publication_gate"] == "official_content_verified_required"
