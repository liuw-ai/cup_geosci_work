"""Deterministic location normalization for public job announcements.

Location is kept separate from employer classification.  A company name can
have offices in several provinces, so this module only derives a province from
the location text carried by the original announcement.
"""

from __future__ import annotations

import re
from typing import Any


PROVINCES = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
)


MAINLAND_COUNTRY_LABELS = frozenset({"中国大陆", "中国"})


_PROVINCE_ALIASES = {
    "北京市": "北京",
    "北京": "北京",
    "天津市": "天津",
    "天津": "天津",
    "河北省": "河北",
    "河北": "河北",
    "山西省": "山西",
    "山西": "山西",
    "内蒙古自治区": "内蒙古",
    "内蒙古": "内蒙古",
    "辽宁省": "辽宁",
    "辽宁": "辽宁",
    "吉林省": "吉林",
    "吉林": "吉林",
    "黑龙江省": "黑龙江",
    "黑龙江": "黑龙江",
    "上海市": "上海",
    "上海": "上海",
    "江苏省": "江苏",
    "江苏": "江苏",
    "浙江省": "浙江",
    "浙江": "浙江",
    "安徽省": "安徽",
    "安徽": "安徽",
    "福建省": "福建",
    "福建": "福建",
    "江西省": "江西",
    "江西": "江西",
    "山东省": "山东",
    "山东": "山东",
    "河南省": "河南",
    "河南": "河南",
    "湖北省": "湖北",
    "湖北": "湖北",
    "湖南省": "湖南",
    "湖南": "湖南",
    "广东省": "广东",
    "广东": "广东",
    "广西壮族自治区": "广西",
    "广西": "广西",
    "海南省": "海南",
    "海南": "海南",
    "重庆市": "重庆",
    "重庆": "重庆",
    "四川省": "四川",
    "四川": "四川",
    "贵州省": "贵州",
    "贵州": "贵州",
    "云南省": "云南",
    "云南": "云南",
    "西藏自治区": "西藏",
    "西藏": "西藏",
    "陕西省": "陕西",
    "陕西": "陕西",
    "甘肃省": "甘肃",
    "甘肃": "甘肃",
    "青海省": "青海",
    "青海": "青海",
    "宁夏回族自治区": "宁夏",
    "宁夏": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "新疆": "新疆",
}


# The list favours provincial capitals and key petroleum/geological locations.
# It is intentionally evidence-only: callers pass original location fields, not
# a job title or employer name, preventing unsafe inferences from a unit name.
_CITY_TO_PROVINCE = {
    "北京": ("北京", "北京"),
    "天津": ("天津", "天津"),
    "石家庄": ("河北", "石家庄"),
    "唐山": ("河北", "唐山"),
    "廊坊": ("河北", "廊坊"),
    "秦皇岛": ("河北", "秦皇岛"),
    "承德": ("河北", "承德"),
    "邢台": ("河北", "邢台"),
    "保定": ("河北", "保定"),
    "太原": ("山西", "太原"),
    "大同": ("山西", "大同"),
    "晋城": ("山西", "晋城"),
    "运城": ("山西", "运城"),
    "吕梁": ("山西", "吕梁"),
    "呼和浩特": ("内蒙古", "呼和浩特"),
    "鄂尔多斯": ("内蒙古", "鄂尔多斯"),
    "沈阳": ("辽宁", "沈阳"),
    "大连": ("辽宁", "大连"),
    "盘锦": ("辽宁", "盘锦"),
    "长春": ("吉林", "长春"),
    "松原": ("吉林", "松原"),
    "哈尔滨": ("黑龙江", "哈尔滨"),
    "大庆": ("黑龙江", "大庆"),
    "上海": ("上海", "上海"),
    "南京": ("江苏", "南京"),
    "苏州": ("江苏", "苏州"),
    "徐州": ("江苏", "徐州"),
    "常州": ("江苏", "常州"),
    "金坛": ("江苏", "常州"),
    "无锡": ("江苏", "无锡"),
    "扬州": ("江苏", "扬州"),
    "淮安": ("江苏", "淮安"),
    "杭州": ("浙江", "杭州"),
    "宁波": ("浙江", "宁波"),
    "衢州": ("浙江", "衢州"),
    "合肥": ("安徽", "合肥"),
    "福州": ("福建", "福州"),
    "厦门": ("福建", "厦门"),
    "南昌": ("江西", "南昌"),
    "济南": ("山东", "济南"),
    "青岛": ("山东", "青岛"),
    "东营": ("山东", "东营"),
    "烟台": ("山东", "烟台"),
    "德州": ("山东", "德州"),
    "郑州": ("河南", "郑州"),
    "南阳": ("河南", "南阳"),
    "濮阳": ("河南", "濮阳"),
    "武汉": ("湖北", "武汉"),
    "宜昌": ("湖北", "宜昌"),
    "荆州": ("湖北", "荆州"),
    "潜江": ("湖北", "潜江"),
    "长沙": ("湖南", "长沙"),
    "广州": ("广东", "广州"),
    "深圳": ("广东", "深圳"),
    "湛江": ("广东", "湛江"),
    "南宁": ("广西", "南宁"),
    "北海": ("广西", "北海"),
    "海口": ("海南", "海口"),
    "三亚": ("海南", "三亚"),
    "重庆": ("重庆", "重庆"),
    "涪陵": ("重庆", "涪陵"),
    "成都": ("四川", "成都"),
    "绵阳": ("四川", "绵阳"),
    "巴中": ("四川", "巴中"),
    "凉山彝族自治州": ("四川", "凉山"),
    "凉山": ("四川", "凉山"),
    "贵阳": ("贵州", "贵阳"),
    "毕节": ("贵州", "毕节"),
    "昆明": ("云南", "昆明"),
    "昭通": ("云南", "昭通"),
    "拉萨": ("西藏", "拉萨"),
    "阿里": ("西藏", "阿里"),
    "西安": ("陕西", "西安"),
    "延安": ("陕西", "延安"),
    "榆林": ("陕西", "榆林"),
    "兰州": ("甘肃", "兰州"),
    "庆阳": ("甘肃", "庆阳"),
    "西宁": ("青海", "西宁"),
    "海西蒙古族藏族自治州": ("青海", "海西"),
    "海西": ("青海", "海西"),
    "银川": ("宁夏", "银川"),
    "乌鲁木齐": ("新疆", "乌鲁木齐"),
    "克拉玛依": ("新疆", "克拉玛依"),
    "库尔勒": ("新疆", "库尔勒"),
    "阿勒泰": ("新疆", "阿勒泰"),
    "塔河油田": ("新疆", "塔河油田"),
}


_OVERSEAS_ALIASES = {
    "国内外": "境内外混合",
    "刚果（金）": "刚果民主共和国",
    "刚果(金)": "刚果民主共和国",
    "刚果民主共和国": "刚果民主共和国",
    "刚果共和国": "刚果共和国",
    "圭亚那": "圭亚那",
    "安哥拉": "安哥拉",
    "阿根廷": "阿根廷",
    "哥伦比亚": "哥伦比亚",
    "哈萨克斯坦": "哈萨克斯坦",
    "利比亚": "利比亚",
    "墨西哥": "墨西哥",
    "泰国": "泰国",
    "乌兹别克斯坦": "乌兹别克斯坦",
    "委内瑞拉": "委内瑞拉",
    "伊拉克": "伊拉克",
    "新加坡": "新加坡",
    "香港": "中国香港",
    "澳门": "中国澳门",
    "台湾": "中国台湾",
    "美国": "美国",
    "加拿大": "加拿大",
    "英国": "英国",
    "挪威": "挪威",
    "阿联酋": "阿联酋",
    "沙特": "沙特阿拉伯",
    "马来西亚": "马来西亚",
    "澳大利亚": "澳大利亚",
    "巴西": "巴西",
    "印度尼西亚": "印度尼西亚",
    "印度": "印度",
    "蒙古国": "蒙古国",
    # These are not countries, but in a work-location field they are explicit
    # non-mainland evidence. A domestic office city must not mask them.
    "非洲": "海外",
    "南美洲": "海外",
    "北美洲": "海外",
    "欧洲": "海外",
    "大洋洲": "海外",
    "东南亚": "海外",
    "中东": "海外",
    "海外": "海外",
    "境外": "海外",
    "国外": "海外",
}

# Public international career systems often provide ISO-like country codes
# rather than a country name.  These are evidence from the supplied location
# field, not an inference from the employer or job title.
_COUNTRY_CODE_ALIASES = {
    "AO": "安哥拉",
    "AE": "阿联酋",
    "AU": "澳大利亚",
    "BR": "巴西",
    "CA": "加拿大",
    "CO": "哥伦比亚",
    "FR": "法国",
    "GB": "英国",
    "ID": "印度尼西亚",
    "IN": "印度",
    "IQ": "伊拉克",
    "KZ": "哈萨克斯坦",
    "LY": "利比亚",
    "MX": "墨西哥",
    "MY": "马来西亚",
    "SG": "新加坡",
    "SR": "苏里南",
    "TH": "泰国",
    "TR": "土耳其",
    "US": "美国",
    "UZ": "乌兹别克斯坦",
    "VE": "委内瑞拉",
}

_OVERSEAS_CITY_ALIASES = {
    "abingdon": "Abingdon",
    "aberdeen": "Aberdeen",
    "abu dhabi": "Abu Dhabi",
    "astana": "Astana",
    "basra": "Basra",
    "benghazi": "Benghazi",
    "bogota": "Bogota",
    "calgary": "Calgary",
    "houston": "Houston",
    "kemaman": "Kemaman",
    "kuala lumpur": "Kuala Lumpur",
    "labuan": "Labuan",
    "luanda": "Luanda",
    "maturin": "Maturin",
    "navi mumbai": "Navi Mumbai",
    "paramaribo": "Paramaribo",
    "phawong": "Phawong",
    "prudhoe bay": "Prudhoe Bay",
    "reforma": "Reforma",
    "rio de janeiro": "Rio de Janeiro",
    "sugar land": "Sugar Land",
    "tashkent": "Tashkent",
    "tripoli": "Tripoli",
    "williston": "Williston",
    "singapore": "Singapore",
}

_ENGLISH_COUNTRIES = (
    (r"\bangola\b", "安哥拉"),
    (r"\bcolombia\b", "哥伦比亚"),
    (r"\b(?:kazakhstan|kz)\b", "哈萨克斯坦"),
    (r"\blibya\b", "利比亚"),
    (r"\bmexico\b", "墨西哥"),
    (r"\bsingapore\b", "新加坡"),
    (r"\bthailand\b", "泰国"),
    (r"\buzbekistan\b", "乌兹别克斯坦"),
    (r"\bvenezuela\b", "委内瑞拉"),
    (r"\biraq\b", "伊拉克"),
    (r"\b(?:united states|usa|u\.s\.)\b", "美国"),
    (r"\bcanada\b", "加拿大"),
    (r"\b(?:united kingdom|uk|england)\b", "英国"),
    (r"\bnorway\b", "挪威"),
    (r"\b(?:uae|united arab emirates)\b", "阿联酋"),
    (r"\bsaudi arabia\b", "沙特阿拉伯"),
    (r"\bmalaysia\b", "马来西亚"),
    (r"\baustralia\b", "澳大利亚"),
    (r"\bbrazil\b", "巴西"),
    (r"\bindonesia\b", "印度尼西亚"),
    (r"\bindia\b", "印度"),
)


_MAINLAND_REGIONAL_MARKERS = (
    "华北",
    "东北",
    "华东",
    "华中",
    "华南",
    "西北",
    "西南",
)


def normalize_location(
    value: str | None,
    *,
    official_province: str | None = None,
) -> dict[str, Any]:
    """Return structured location fields from explicit announcement text.

    ``explicit`` means the announcement itself names a province/region.  A
    city maps to its province as ``normalized`` because the province is a
    deterministic geographic normalization, rather than a claim in the source.

    ``official_province`` is reserved for a province column on the same
    official government position-table row. It is only considered after the
    raw work-location field has no non-mainland evidence and cannot otherwise
    be mapped. Callers must not pass an employer's registration province or a
    source-level province as a substitute for a row-level work location.
    """
    raw = _clean(value)
    result: dict[str, Any] = {
        "province": None,
        "city": None,
        "country_or_region": None,
        "location_confidence": "unknown",
        "location_evidence": None,
    }
    if not raw:
        return result

    # A location can name both a domestic office and an overseas assignment,
    # such as "北京，非洲". The public corpus is mainland-only, so foreign
    # evidence must take precedence over a domestic token.
    foreign = _foreign_location_evidence(raw)
    if foreign is not None:
        foreign_evidence, country = foreign
        mainland_evidence = _mainland_location_evidence(raw)
        if mainland_evidence:
            result.update(
                {
                    "country_or_region": "境内外混合",
                    "location_confidence": "explicit",
                    "location_evidence": f"{mainland_evidence}；{foreign_evidence}",
                }
            )
        else:
            result.update(
                {
                    "city": (
                        _city_from_overseas_location(raw)
                        if re.search(r"[A-Za-z]", raw)
                        else None
                    ),
                    "country_or_region": country,
                    "location_confidence": "explicit",
                    "location_evidence": foreign_evidence,
                }
            )
        return result

    # A location field that explicitly says "全国" describes a mainland-wide
    # opportunity.  It is domestic evidence, but deliberately has no province
    # so it cannot inflate any individual province's coverage.
    if raw in {"全国", "全国项目地", "全国范围", "全国各地"}:
        result.update(
            {
                "country_or_region": "中国大陆",
                "location_confidence": "explicit",
                "location_evidence": "全国",
            }
        )
        return result

    province_match: tuple[str, str] | None = None
    for alias, province in _sorted_aliases(_PROVINCE_ALIASES):
        if alias in raw:
            province_match = (alias, province)
            break

    for alias, (province, city) in _sorted_aliases(_CITY_TO_PROVINCE):
        if alias in raw:
            confidence = "explicit" if province_match is not None else "normalized"
            evidence = province_match[0] if province_match is not None else alias
            result.update(
                {
                    "province": province,
                    "city": city,
                    "country_or_region": "中国大陆",
                    "location_confidence": confidence,
                    "location_evidence": evidence,
                }
            )
            return result

    if province_match is not None:
        alias, province = province_match
        result.update(
            {
                "province": province,
                "city": province if province in {"北京", "天津", "上海", "重庆"} else None,
                "country_or_region": "中国大陆",
                "location_confidence": "explicit",
                "location_evidence": alias,
            }
        )
        return result

    # A field such as "长期驻外，如西北、西南等" is still an explicit
    # mainland-region statement when it contains no foreign marker. It has no
    # single province, so it must not contribute to province coverage.
    for marker in _MAINLAND_REGIONAL_MARKERS:
        if marker in raw:
            result.update(
                {
                    "country_or_region": "中国大陆",
                    "location_confidence": "explicit",
                    "location_evidence": marker,
                }
            )
            return result

    province_from_row = _official_province(official_province)
    if province_from_row is not None:
        evidence = _clean(official_province)
        result.update(
            {
                "province": province_from_row,
                "city": (
                    province_from_row
                    if province_from_row in {"北京", "天津", "上海", "重庆"}
                    else None
                ),
                "country_or_region": "中国大陆",
                "location_confidence": "official_row_province",
                "location_evidence": f"官方职位表省份：{evidence}",
            }
        )
        return result

    # An arbitrary Chinese place name may not yet be in the city map. It must
    # remain unclassified until reviewed, not become "海外" because it has a
    # comma. The fallback below is only for Latin job-board locations.
    city = _city_from_overseas_location(raw) if re.search(r"[A-Za-z]", raw) else None
    if city:
        result.update(
            {
                "city": city,
                "country_or_region": "海外",
                "location_confidence": "normalized",
                "location_evidence": city,
            }
        )
        return result

    return result


def _mainland_location_evidence(raw: str) -> str | None:
    """Return an explicit mainland marker without inferring it from employer text."""

    if raw in {"全国", "全国项目地", "全国范围", "全国各地"}:
        return "全国"
    for alias, _province in _sorted_aliases(_PROVINCE_ALIASES):
        if alias in raw:
            return alias
    for alias, _location in _sorted_aliases(_CITY_TO_PROVINCE):
        if alias in raw:
            return alias
    return None


def _official_province(value: str | None) -> str | None:
    """Normalize an exact province field from one official position-table row."""

    cleaned = _clean(value)
    if not cleaned:
        return None
    for alias, province in _sorted_aliases(_PROVINCE_ALIASES):
        if cleaned == alias:
            return province
    return None


def _foreign_location_evidence(raw: str) -> tuple[str, str] | None:
    """Find explicit non-mainland evidence before domestic normalization."""

    for alias, country in _sorted_aliases(_OVERSEAS_ALIASES):
        if alias in raw:
            return alias, country
    lowered = raw.lower()
    for pattern, country in _ENGLISH_COUNTRIES:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0), country
    for code, country in _COUNTRY_CODE_ALIASES.items():
        if re.search(rf"(?<![A-Z]){re.escape(code)}(?![A-Z])", raw.upper()):
            return code, country
    return None


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _sorted_aliases(mapping: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True)


def extract_location_hint(text: str | None) -> str | None:
    """Extract an explicitly labelled location from an official announcement.

    This intentionally does not inspect the employer name.  It is a small
    fallback for portals that put ``工作地点`` or ``Location`` in the body
    instead of exposing a dedicated location field.
    """
    cleaned = _clean(text)
    if not cleaned:
        return None
    pattern = re.compile(
        r"(?:工作地点|工作地|工作城市|任职地点|岗位地点|工作区域|地点|"
        r"location|city|country)\s*[:：]\s*"
        r"(.{1,120}?)(?=\s*(?:[；;。|]\s*)?(?:学历|专业|职位|岗位|职责|要求|任职资格|"
        r"发布时间|截止|申请|报名|福利|department|job\s+description|"
        r"requirements|qualifications|responsibilities)\s*[:：]|$)",
        re.IGNORECASE,
    )
    match = pattern.search(cleaned)
    if not match:
        return None
    value = re.split(r"[。；;|]", match.group(1), maxsplit=1)[0].strip(" ,，")
    return value[:120] or None


def _city_from_overseas_location(raw: str) -> str | None:
    lowered = raw.lower()
    if "multi-location" in lowered or "multiple locations" in lowered:
        return "多地点"
    for alias, city in sorted(
        _OVERSEAS_CITY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if alias in lowered:
            return city
    # For a comma-delimited international location, the first component is
    # normally the city.  Keep it only when it is not a generic label.
    first = re.split(r"[,，]", raw, maxsplit=1)[0].strip()
    if first and first.lower() not in {"multi-location", "multiple locations", "海外"}:
        return first[:80]
    return None
