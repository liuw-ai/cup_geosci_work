from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


from job_hub.employers import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_ORDER,
    classify_employment,
)


DEGREE_ORDER = ("本科", "硕士", "博士")

RECRUITMENT_WORDS = (
    "招聘",
    "招录",
    "招考",
    "招募",
    "校招",
    "校园招聘",
    "社会招聘",
    "公开招聘",
    "人才引进",
    "选调",
    "公务员",
    "博士后",
    "科研助理",
    "实习生",
    "实习",
    "job",
    "jobs",
    "career",
    "careers",
    "hiring",
    "vacancy",
    "vacancies",
    "internship",
    "graduate program",
    "postdoctoral",
)

CORE_MAJOR_KEYWORDS = {
    "资源勘查工程": 32,
    "资源勘探工程": 28,
    "资源勘探": 24,
    "资源勘查": 28,
    "勘查技术与工程": 28,
    "矿产普查与勘探": 28,
    "地质工程": 32,
    "地质学": 30,
    "地质资源与地质工程": 35,
    "地质资源": 28,
    "石油地质": 30,
    "油气地质": 30,
    "地球物理": 24,
    "地球物理学": 24,
    "地球探测与信息技术": 26,
    "地球化学": 22,
    "矿产勘查": 28,
    "地质调查": 26,
    "物探": 28,
    "地球物理勘探": 28,
    "地震勘探": 26,
    "地质勘探": 26,
    "地质勘查": 26,
    "工程地质": 24,
    "水文地质": 22,
    "环境地质": 20,
    "地质灾害": 20,
    "地震解释": 24,
    "测井": 26,
    "储层": 24,
    "油藏": 22,
    "勘探开发": 22,
    "钻井地质": 26,
    "地热": 18,
    "遥感": 16,
    "gis": 16,
    "地理信息": 16,
    "测绘": 14,
    "矿业工程": 18,
    "地球科学": 28,
    "地貌": 20,
    "海岸带": 20,
    "海洋地质": 24,
    "海洋": 14,
    "沉积": 18,
    "古生物": 18,
    "资源环境": 18,
    "地学": 18,
    "石油工程": 20,
    "geoscience": 28,
    "geologist": 28,
    "geology": 28,
    "geological science": 28,
    "geological sciences": 28,
    "geological engineering": 30,
    "geological resources and engineering": 32,
    "resource exploration engineering": 30,
    "geophysical": 24,
    "geophysics": 24,
    "geophysicist": 24,
    "petrophysics": 26,
    "petrophysical": 26,
    "well logging": 26,
    "welllog": 26,
    "cased hole": 22,
    "mud logging": 22,
    "formation evaluation": 24,
    "completion": 18,
    "completions": 18,
    "perforating": 18,
    "directional drilling": 20,
    "seismic": 24,
    "reservoir": 24,
    "subsurface": 24,
    "petroleum": 22,
    "oilfield": 22,
    "drilling": 18,
    "wireline": 22,
    "mineral exploration": 26,
    "mining engineering": 18,
    "remote sensing": 16,
    "geospatial": 16,
}

OIL_AND_ENERGY_KEYWORDS = (
    "石油",
    "天然气",
    "油气",
    "油田",
    "炼化",
    "油服",
    "能源",
    "管网",
    "页岩",
    "新能源",
    "碳储存",
    "ccus",
    "petroleum",
    "oilfield",
    "oil and gas",
    "geothermal",
    "carbon storage",
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = value.replace("\u3000", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith(("utm_", "spm", "from"))
        ]
    )
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def stable_hash(*values: str) -> str:
    joined = "\n".join(clean_text(value) for value in values)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def looks_like_recruitment(text: str) -> bool:
    lowered = clean_text(text).lower()
    return any(word.lower() in lowered for word in RECRUITMENT_WORDS)


def extract_degree_levels(text: str) -> list[str]:
    lowered = clean_text(text).lower()
    found: list[str] = []
    if (
        "本科" in lowered
        or "学士" in lowered
        or "bachelor" in lowered
        or "undergraduate" in lowered
    ):
        found.append("本科")
    if "硕士" in lowered or "研究生" in lowered or "master" in lowered:
        found.append("硕士")
    if (
        "博士" in lowered
        or "博士后" in lowered
        or "ph.d" in lowered
        or "phd" in lowered
        or "doctoral" in lowered
        or "postdoctoral" in lowered
    ):
        found.append("博士")
    return found


def extract_major_tags(text: str) -> list[str]:
    lowered = clean_text(text).lower()
    found = [
        keyword
        for keyword in CORE_MAJOR_KEYWORDS
        if keyword.lower() in lowered
    ]
    return found[:8]


def classify_category(
    text: str,
    source_category: str | None = None,
    *,
    identity_text: str | None = None,
) -> str:
    return classify_employment(
        text,
        source_category=source_category,
        identity_text=identity_text,
    ).category


def score_relevance(
    text: str,
    source_tier: str,
    category: str,
) -> tuple[int, str, list[str]]:
    lowered = clean_text(text).lower()
    matched_tags = extract_major_tags(lowered)
    score = 0

    if source_tier == "A":
        score += 20
    elif source_tier == "B":
        score += 12
    else:
        score += 5

    score += min(sum(CORE_MAJOR_KEYWORDS[tag] for tag in matched_tags), 58)
    if any(keyword in lowered for keyword in OIL_AND_ENERGY_KEYWORDS):
        score += 15
    if category in {
        "油气上游业主与研究机构",
        "油气工程技术服务",
        "管网、炼化与综合能源",
    }:
        score += 18
    elif category in {
        "自然资源、地调与地勘",
        "矿产资源与矿业",
        "地质工程、环境与基础设施",
        "事业单位与人才引进",
        "公务员与选调",
        "科研院所、高校与博士后",
    }:
        score += 10

    if "中国石油大学" in lowered or "石油大学" in lowered:
        score += 8

    if score >= 55:
        return min(score, 100), "强相关", matched_tags
    if score >= 30:
        return min(score, 100), "相关机会", matched_tags
    return min(score, 100), "拓展机会", matched_tags


def parse_chinese_date(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _normalize_date_text(value)
    match = re.search(
        r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})",
        normalized,
    )
    if not match:
        return None
    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ).isoformat()
    except ValueError:
        return None


def parse_date_value(value: str | None) -> str | None:
    """Parse public Chinese and English date strings without locale dependence."""
    parsed = parse_chinese_date(value)
    if parsed:
        return parsed
    normalized = _normalize_date_text(value)
    for pattern in (
        r"([A-Z][a-z]{2,8}\s+\d{1,2},\s*20\d{2})",
        r"(\d{1,2}\s+[A-Z][a-z]{2,8}\s+20\d{2})",
    ):
        match = re.search(pattern, normalized)
        if not match:
            continue
        for format_string in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(match.group(1), format_string).date().isoformat()
            except ValueError:
                continue
    return None


def extract_deadline(text: str) -> str | None:
    normalized = _normalize_date_text(text)
    # Some rolling announcements describe a short first batch and then a
    # later overall closing date. Prefer the explicit overall "至 YYYY-MM-DD"
    # boundary over an earlier nested "第一批次截止" date.
    overall_window = re.compile(
        r"(?:报名|申请|网申|投递|应聘).{0,24}?(?:时间|期间|日期)"
        r".{0,32}?(?:自|从).{0,24}?"
        r"(?:至|到)\s*(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})",
        re.IGNORECASE,
    )
    match = overall_window.search(normalized)
    if match:
        return parse_date_value(match.group(1))
    range_pattern = re.compile(
        r"(?:报名|申请|网申|投递|应聘).{0,24}?(?:时间|期间|日期)"
        r".{0,20}?(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})"
        r"\s*(?:至|到|-|—|~)\s*"
        r"(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})",
        re.IGNORECASE,
    )
    match = range_pattern.search(normalized)
    if match:
        return parse_date_value(match.group(2))
    # Government recruitment notices commonly write a range such as
    # "2026年9月9日9:00至9月16日17:00".  The end of the range deliberately
    # omits its year, so the full-date pattern above cannot see it.  Treat the
    # end date as the same year only inside an explicit application window.
    implicit_year_range = re.compile(
        r"(?:报名|申请|网申|投递|应聘).{0,24}?(?:时间|期间|日期)"
        r".{0,32}?(?P<start_year>20\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
        r"(?:\s*\d{1,2}:\d{2})?\s*(?:至|到|-|—|~)\s*"
        r"(?:(?P<end_year>20\d{2})\s*年\s*)?"
        r"(?P<end_month>\d{1,2})\s*月\s*(?P<end_day>\d{1,2})\s*日",
        re.IGNORECASE,
    )
    match = implicit_year_range.search(normalized)
    if match:
        try:
            return date(
                int(match.group("end_year") or match.group("start_year")),
                int(match.group("end_month")),
                int(match.group("end_day")),
            ).isoformat()
        except ValueError:
            pass
    pattern = re.compile(
        r"(?:报名|申请|网申|投递|应聘|招聘).{0,24}?(?:截止|截至|截止时间|结束)"
        r".{0,32}?(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})",
        re.IGNORECASE,
    )
    match = pattern.search(normalized)
    if match:
        return parse_date_value(match.group(1))
    expiry_pattern = re.compile(
        r"(?:过期时间|有效期(?:至|截止)?|到期时间)\s*[:：]?\s*"
        r"(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})",
        re.IGNORECASE,
    )
    match = expiry_pattern.search(normalized)
    if match:
        return parse_date_value(match.group(1))
    english_pattern = re.compile(
        r"(?:application\s+deadline|closing\s+date|deadline|apply\s+by)"
        r"[:\s]{0,16}([A-Z][a-z]{2,8}\s+\d{1,2},\s*20\d{2})",
        re.IGNORECASE,
    )
    match = english_pattern.search(normalized)
    if match:
        return parse_date_value(match.group(1))
    return None


def extract_published_date(text: str) -> str | None:
    normalized = _normalize_date_text(text)
    marker = re.search(
        r"(?:发布时间|发布于|发布日期|公告日期|发文日期|更新日期)[:：\s]{0,8}"
        r"(20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2})",
        normalized,
        re.IGNORECASE,
    )
    if marker:
        return parse_date_value(marker.group(1))
    if re.search(r"(?:截止|截至|过期|有效期|到期)", normalized):
        return None
    return parse_date_value(normalized)


def is_expired(deadline_date: str | None, today: date) -> bool:
    if not deadline_date:
        return False
    try:
        return date.fromisoformat(deadline_date) < today
    except ValueError:
        return False


def _normalize_date_text(value: str) -> str:
    """Repair spaces inserted between digit spans by rich-text announcement HTML."""
    return re.sub(r"(?<=\d)\s+(?=\d)", "", clean_text(value))
