from __future__ import annotations

import pytest

from scripts.capture_phase4_cnooc_api_unit_evidence import (
    _normalize_job,
    _payload,
    _sha256_bytes,
)


def test_cnooc_api_payload_is_bounded_and_official_campaign_scoped() -> None:
    payload = _payload(2, 100)
    assert payload["orgNumbers"] == ["105147"]
    assert payload["pageIndex"] == 2
    assert payload["pageSize"] == 100
    assert payload["jobSource"] == 2


def test_cnooc_api_normalization_preserves_official_detail_and_delivery_links() -> None:
    row = _normalize_job(
        {
            "company": {"campusOrgName": "中海石油（中国）有限公司深圳分公司"},
            "job": {
                "jobNumber": "CC258591510J40972459115",
                "title": "勘探地质助理工程师",
                "cityName": "深圳",
                "address": "深圳市南山区",
                "minEducationName": "硕士",
                "needMajor": "",
                "detail": "专业要求：地质学类，地质工程；学历要求：硕士研究生及以上",
                "url": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459115",
                "deliveryPath": "https://campus1.zhaopin.com/Resume/CheckIntoApp?pid=CC258591510J40972459115",
            },
        }
    )
    assert row["employer"] == "中海石油（中国）有限公司深圳分公司"
    assert row["geoscience_candidate"] is True
    assert row["detail_url"].startswith("https://xiaoyuan.zhaopin.com/job/")
    assert row["official_application_url"].startswith("https://campus1.zhaopin.com/Resume/")


def test_cnooc_api_normalization_rejects_non_official_delivery_host() -> None:
    with pytest.raises(ValueError, match="delivery_url host"):
        _normalize_job(
            {
                "company": {"campusOrgName": "示例单位"},
                "job": {
                    "jobNumber": "1",
                    "title": "地质工程师",
                    "url": "https://xiaoyuan.zhaopin.com/job/1",
                    "deliveryPath": "https://example.com/apply/1",
                    "detail": "地质工程",
                },
            }
        )


def test_cnooc_api_raw_response_hash_is_sha256() -> None:
    digest = _sha256_bytes(b"cnooc")
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)
