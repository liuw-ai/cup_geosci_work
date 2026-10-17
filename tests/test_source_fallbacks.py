from __future__ import annotations

import copy

import pytest

from job_hub.contracts import ContractValidationError
from job_hub.discovery import resolve_official_url_candidates
from job_hub.source_fallbacks import (
    build_fallback_audit,
    load_fallback_plan,
    validate_fallback_plan,
)


def test_default_fallback_plan_covers_cnpc_and_sinopec_without_claiming_readiness() -> None:
    plan = load_fallback_plan()
    report = build_fallback_audit(
        plan,
        source_records=[{"id": "cnpc-career"}, {"id": "sinopec-career"}],
    )

    assert {item["group_id"] for item in report["entries"]} == {"cnpc", "sinopec"}
    assert report["entry_count"] == 6
    assert report["candidate_count"] == 4
    assert report["access_limited_count"] == 2
    assert report["verified_count"] == 0
    assert report["enabled_source_count"] == 0
    assert report["automation_ready_count"] == 0
    assert report["policy"]["candidate_is_not_source"] is True


def test_verified_authorized_platform_requires_official_authorization() -> None:
    payload = copy.deepcopy(load_fallback_plan())
    entry = payload["groups"][0]["entries"][2]
    entry["status"] = "verified"
    entry["evidence_url"] = "https://www.cnpc.com.cn/zh/rczp/"

    with pytest.raises(ContractValidationError, match="authorization_evidence_url"):
        validate_fallback_plan(payload)


def test_verified_fallback_must_bind_a_registered_source_before_ready() -> None:
    payload = copy.deepcopy(load_fallback_plan())
    entry = payload["groups"][0]["entries"][0]
    entry["status"] = "verified"
    entry["evidence_url"] = "https://www.cnpc.com.cn/zh/rczp/"
    report = build_fallback_audit(
        validate_fallback_plan(payload),
        source_records=[{"id": "cnpc-career"}],
    )

    assert report["verified_count"] == 1
    assert report["enabled_source_count"] == 0
    assert report["automation_ready_count"] == 0


def test_registered_official_fallback_can_resolve_a_private_lead_without_publishing() -> None:
    plan = load_fallback_plan()
    records = [
        {
            "id": "cnpc-official-announcement-fallback",
            "homepage_url": "https://www.cnpc.com.cn/zh/rczp/",
            "config": {"allowed_hosts": ["www.cnpc.com.cn", "cnpc.com.cn"]},
        }
    ]
    recovery = {
        "official_link_candidates": [
            {"url": "https://www.cnpc.com.cn/zh/rczp/2027/notice.html", "label": "官方公告"}
        ]
    }

    resolved = resolve_official_url_candidates(recovery, records)
    assert resolved == [
        {
            "url": "https://www.cnpc.com.cn/zh/rczp/2027/notice.html",
            "label": "官方公告",
            "host": "www.cnpc.com.cn",
                "official_source_id": "cnpc-official-announcement-fallback",
                "domain_status": "registered_source_match",
                "candidate_quality": "job_detail_or_notice",
                "candidate_kind": "official_page",
        }
    ]
    # The fallback plan remains non-runnable until official content is checked;
    # the latest access-policy probe is retained as access_limited evidence.
    assert next(
        item for item in plan["groups"][0]["entries"]
        if item["entry_id"] == "cnpc-official-announcement"
    )["status"] == "access_limited"
