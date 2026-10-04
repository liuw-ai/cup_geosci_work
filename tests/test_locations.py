from __future__ import annotations

from job_hub.employers import resolve_employer
from job_hub.locations import extract_location_hint, normalize_location


def test_location_normalization_uses_explicit_location_evidence() -> None:
    direct = normalize_location("北京市海淀区 / 西安")
    city = normalize_location("克拉玛依市")
    nationwide = normalize_location("全国项目地")
    jincheng = normalize_location("晋城-沁水县")
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
    assert nationwide == {
        "province": None,
        "city": None,
        "country_or_region": "中国大陆",
        "location_confidence": "explicit",
        "location_evidence": "全国",
    }
    assert normalize_location("全国油气地质大赛")["country_or_region"] != "中国大陆"
    assert jincheng["province"] == "山西"
    assert jincheng["city"] == "晋城"
    assert jincheng["country_or_region"] == "中国大陆"
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


def test_foreign_evidence_precedes_domestic_prefix_and_known_chinese_cities() -> None:
    mixed = normalize_location("北京，非洲")
    congo = normalize_location("刚果（金）")
    domestic_cities = normalize_location("邢台，保定")

    assert mixed["country_or_region"] == "境内外混合"
    assert mixed["province"] is None
    assert mixed["location_evidence"] == "北京；非洲"
    assert congo["country_or_region"] == "刚果民主共和国"
    assert domestic_cities["country_or_region"] == "中国大陆"
    assert domestic_cities["province"] == "河北"


def test_official_position_table_province_is_narrow_fallback_only() -> None:
    official_row = normalize_location("未收录工作地", official_province="河南")
    foreign_row = normalize_location("Calgary, AB, CA", official_province="北京")
    mixed_row = normalize_location("北京，非洲", official_province="北京")

    assert official_row == {
        "province": "河南",
        "city": None,
        "country_or_region": "中国大陆",
        "location_confidence": "official_row_province",
        "location_evidence": "官方职位表省份：河南",
    }
    assert foreign_row["country_or_region"] == "加拿大"
    assert mixed_row["country_or_region"] == "境内外混合"


def test_verified_operational_locations_keep_mixed_assignments_private() -> None:
    expected = {
        "塔河油田": "新疆",
        "南阳": "河南",
        "荆州、潜江": "湖北",
        "扬州、淮安及油田业务所在地": "江苏",
        "阿勒泰-哈巴河县": "新疆",
    }

    for raw, province in expected.items():
        normalized = normalize_location(raw)
        assert normalized["country_or_region"] == "中国大陆"
        assert normalized["province"] == province

    assert normalize_location("国内外项目现场")["country_or_region"] == "境内外混合"
    regional = normalize_location("长期驻外，如西北、西南等")
    assert regional["country_or_region"] == "中国大陆"
    assert regional["province"] is None
    assert normalize_location("蒙古国")["country_or_region"] == "蒙古国"


def test_verified_city_and_autonomous_prefecture_aliases_normalize_to_mainland() -> None:
    expected = {
        "运城-河津市": "山西",
        "昭通-昭阳区": "云南",
        "毕节-赫章县": "贵州",
        "海西蒙古族藏族自治州-格尔木市": "青海",
        "凉山彝族自治州-会理市": "四川",
        "阿里-改则县": "西藏",
    }

    for raw, province in expected.items():
        normalized = normalize_location(raw)
        assert normalized["province"] == province
        assert normalized["country_or_region"] == "中国大陆"


def test_cnooc_detail_city_aliases_normalize_to_mainland() -> None:
    expected = {
        "荆门 钟祥市 胡集镇": "湖北",
        "东方 八所镇园区": "海南",
        "鹤岗 兴安区": "黑龙江",
        "滨州 滨城区": "山东",
        "汕尾": "广东",
        "惠州 大亚湾区": "广东",
        "珠海 金湾区": "广东",
        "中山 横门路": "广东",
        "盐城 滨海县": "江苏",
        "绍兴 诸暨市": "浙江",
        "营口 盖州市": "辽宁",
        "洋浦市/洋浦经济开发区": "海南",
    }

    for raw, province in expected.items():
        normalized = normalize_location(raw)
        assert normalized["province"] == province
        assert normalized["country_or_region"] == "中国大陆"
