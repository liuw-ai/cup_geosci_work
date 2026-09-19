from __future__ import annotations

from job_hub.employers import resolve_employer
from job_hub.locations import extract_location_hint, normalize_location


def test_location_normalization_uses_explicit_location_evidence() -> None:
    direct = normalize_location("北京市海淀区 / 西安")
    city = normalize_location("克拉玛依市")
    unknown = normalize_location(None)

    assert direct == {
        "province": "北京",
        "city": "北京",
        "country_or_region": "中国大陆",
        "location_confidence": "explicit",
        "location_evidence": "北京市",
    }
    assert city["province"] == "新疆"
    assert city["city"] == "克拉玛依"
    assert city["location_confidence"] == "normalized"
    assert unknown["province"] is None
    assert unknown["location_confidence"] == "unknown"


def test_employer_registry_keeps_technical_service_subsidiary_distinct() -> None:
    resolved = resolve_employer("中国石油东方物探公司")
    cosl = resolve_employer("中海油服天津分公司")

    assert resolved is not None
    assert resolved["canonical_employer_id"] == "cnpc-bgp"
    assert resolved["parent_employer_name"] == "中国石油天然气集团有限公司"
    assert resolved["category"] == "油气工程技术服务"
    assert cosl is not None
    assert cosl["canonical_employer_id"] == "cosl"
    assert cosl["affiliation"] == "中国海油体系"


def test_location_normalization_supports_international_codes_and_labeled_body_text() -> None:
    overseas = normalize_location("Calgary, AB, CA, T2P 3V4")
    labeled = extract_location_hint("工作地点：新疆克拉玛依；学历要求：硕士")

    assert overseas["city"] == "Calgary"
    assert overseas["country_or_region"] == "加拿大"
    assert overseas["province"] is None
    assert labeled == "新疆克拉玛依"
