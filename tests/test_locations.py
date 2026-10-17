from __future__ import annotations

from job_hub.employers import resolve_employer
from job_hub.locations import (
    extract_location_hint,
    extract_location_provinces,
    extract_structured_location_candidates,
    normalize_location,
)
from job_hub.pipeline import _resolve_location_evidence


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
    assert normalize_location("厄瓜多尔")["country_or_region"] == "厄瓜多尔"
    assert normalize_location("俄罗斯")["country_or_region"] == "俄罗斯"
    assert unknown["province"] is None
    assert unknown["location_confidence"] == "unknown"


def test_split_location_does_not_fall_back_to_another_province() -> None:
    raw = "福州-闽侯县，塔城-托里县，山南"
    for variant, province in (
        ("福州-闽侯县", "福建"),
        ("塔城-托里县", "新疆"),
        ("山南", "西藏"),
    ):
        text, normalized = _resolve_location_evidence(
            variant,
            field_evidence={
                "地点拆分基准岗位": "official-job-1",
                "官方工作地点": variant,
                "工作地点": raw,
            },
            fallback_text=raw,
            official_province=None,
            allow_unclassified_domestic_address=False,
        )
        assert text == variant
        assert normalized["province"] == province

    text, normalized = _resolve_location_evidence(
        "未识别县域",
        field_evidence={
            "地点拆分基准岗位": "official-job-1",
            "官方工作地点": "未识别县域",
            "工作地点": raw,
        },
        fallback_text=raw,
        official_province=None,
        allow_unclassified_domestic_address=False,
    )
    assert text == "未识别县域"
    assert normalized["country_or_region"] is None


def test_historical_split_row_prefers_bound_official_variant() -> None:
    """A legacy combined location must not classify every variant as domestic."""

    text, normalized = _resolve_location_evidence(
        "上海，厄瓜多尔",
        field_evidence={
            "evidence_scope": "official_iguopin_browser_detail",
            "地点拆分基准岗位": "iguopin-general-1",
            "官方工作地点": "厄瓜多尔",
            "工作地点": "厄瓜多尔",
        },
        fallback_text="工作地点：厄瓜多尔",
        official_province=None,
        allow_unclassified_domestic_address=False,
    )

    assert text == "厄瓜多尔"
    assert normalized["country_or_region"] == "厄瓜多尔"


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


def test_three_oil_platform_prefix_does_not_hide_concrete_subsidiary() -> None:
    tianjin = resolve_employer("中国海油-中海石油（中国）有限公司天津分公司")
    daqing = resolve_employer("中国石油天然气股份有限公司大庆油田有限责任公司")
    southwest = resolve_employer("中国石油天然气股份有限公司西南油气田分公司")
    research = resolve_employer("中国海油-中海油研究总院有限责任公司")

    assert tianjin is not None
    assert tianjin["canonical_employer_id"] == "cnooc-tianjin"
    assert tianjin["parent_employer_name"] == "中国海洋石油集团有限公司"
    assert daqing is not None
    assert daqing["canonical_employer_id"] == "cnpc-daqing"
    assert southwest is not None
    assert southwest["canonical_employer_id"] == "cnpc-southwest-gas"
    assert research is not None
    assert research["canonical_employer_id"] == "cnooc-research"


def test_employer_registry_maps_new_cnpc_research_and_engineering_aliases() -> None:
    from job_hub.employers import employment_segment

    engineering = {
        "employer": "中国石油工程建设有限公司新疆设计分公司",
        "source_id": "cupb-career",
        "country_or_region": "中国大陆",
    }
    research = {
        "employer": "中国石油集团经济技术研究院",
        "source_id": "cupb-career",
        "country_or_region": "中国大陆",
    }

    assert resolve_employer(engineering["employer"])["canonical_employer_id"] == (
        "cnpc-engineering-construction"
    )
    assert resolve_employer(research["employer"])["canonical_employer_id"] == (
        "cnpc-economic-research"
    )
    assert employment_segment(engineering) == "three_oil"
    assert employment_segment(research) == "three_oil"


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


def test_location_province_index_preserves_multiple_domestic_provinces() -> None:
    assert extract_location_provinces("北京、山东") == ["北京", "山东"]
    assert extract_location_provinces("荆州、潜江") == ["湖北"]
    assert extract_location_provinces("全国各地") == []
    assert extract_location_provinces("北京，非洲") == []
    assert extract_location_provinces("未收录工作地", official_province="河南") == ["河南"]


def test_structured_location_candidates_keep_detail_scope() -> None:
    candidates = extract_structured_location_candidates(
        {
            "工作地点": "左子坪乡",
            "专业要求": "地质工程；工作地点：山西吕梁；学历要求：硕士",
            "招聘单位": "不应被读取的单位注册地：北京",
        }
    )

    assert candidates[:2] == ["左子坪乡", "山西吕梁"]
    assert "北京" not in candidates


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


def test_official_capture_city_aliases_cover_unclassified_mainland_locations() -> None:
    expected = {
        "德阳-旌阳区": ("四川", "德阳"),
        "桂林-七星区": ("广西", "桂林"),
        "雄安新区-容城": ("河北", "雄安新区"),
        "锡林郭勒-西乌珠穆沁旗": ("内蒙古", "西乌珠穆沁旗"),
        "伊犁": ("新疆", "伊犁"),
        "酒泉": ("甘肃", "酒泉"),
        "岳阳-平江县": ("湖南", "岳阳"),
        "汕尾": ("广东", "汕尾"),
        "迪庆-香格里拉市": ("云南", "迪庆"),
        "南通-崇川区": ("江苏", "南通"),
        "铜陵-铜官区": ("安徽", "铜陵"),
        "曲靖-会泽县": ("云南", "曲靖"),
        "中卫-沙坡头区": ("宁夏", "中卫"),
        "中国": (None, None),
    }

    for raw, (province, city) in expected.items():
        normalized = normalize_location(raw)
        assert normalized["province"] == province
        assert normalized["city"] == city
        assert normalized["country_or_region"] == "中国大陆"

    # An explicit foreign marker still wins over a city alias.
    mixed = normalize_location("桂林，蒙古国")
    assert mixed["country_or_region"] == "境内外混合"


def test_official_capture_city_aliases_cover_recent_geology_rows() -> None:
    expected = {
        "上饶": ("江西", "上饶"),
        "临汾": ("山西", "临汾"),
        "忻州-忻府区": ("山西", "忻州"),
        "自贡市": ("四川", "自贡"),
    }
    for raw, (province, city) in expected.items():
        normalized = normalize_location(raw)
        assert normalized["province"] == province
        assert normalized["city"] == city
        assert normalized["country_or_region"] == "中国大陆"


def test_iguopin_detail_location_aliases_distinguish_mainland_and_overseas() -> None:
    """Use only a detail's own work-location field, never its employer."""

    mainland = {
        "邯郸，鞍山，营口，六安，芜湖": {"河北", "辽宁", "安徽"},
        "淮北-相山区": {"安徽"},
        "珠海市-横琴粤澳深度合作区": {"广东"},
        "绍兴-越城区": {"浙江"},
        "昌江": {"海南"},
        "巴音郭楞-若羌县": {"新疆"},
    }
    for raw, provinces in mainland.items():
        normalized = normalize_location(raw)
        assert normalized["country_or_region"] == "中国大陆"
        assert set(extract_location_provinces(raw)) == provinces

    for raw, country in {
        "赞比亚": "赞比亚",
        "塔吉克斯坦": "塔吉克斯坦",
        "巴布亚新几内亚": "巴布亚新几内亚",
        "巴基斯坦": "巴基斯坦",
    }.items():
        assert normalize_location(raw)["country_or_region"] == country


def test_reviewed_official_detail_address_can_be_domestic_without_city_inference() -> None:
    address = normalize_location(
        "南调路1388号",
        allow_unclassified_domestic_address=True,
    )
    assert address["country_or_region"] == "中国大陆"
    assert address["location_confidence"] == "official_detail_address"
    assert address["province"] is None
    assert address["city"] is None

    # The opt-in is source-scoped and must not turn an arbitrary label into a
    # mainland location. Explicit overseas evidence still takes precedence.
    assert normalize_location(
        "海外项目现场",
        allow_unclassified_domestic_address=True,
    )["country_or_region"] != "中国大陆"
    assert normalize_location(
        "未知工作地",
        allow_unclassified_domestic_address=True,
    )["country_or_region"] is None
