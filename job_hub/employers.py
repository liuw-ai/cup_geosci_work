"""Employment-path taxonomy for CUPB geoscience job information.

The site classifies an opportunity by the employer's role in the industry
chain.  This is deliberately different from a broad label such as
"three oil companies and oilfield services": an operator, a group-owned
technical-service company, an independent service company, and an overseas
role answer different questions for a student.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from job_hub.contracts import ContractValidationError, validate_employer_registry


CATEGORY_ORDER = (
    "油气上游业主与研究机构",
    "油气工程技术服务",
    "管网、炼化与综合能源",
    "自然资源、地调与地勘",
    "矿产资源与矿业",
    "地质工程、环境与基础设施",
    "科研院所、高校与博士后",
    "事业单位与人才引进",
    "公务员与选调",
    "金融与央国企综合机会",
    "能源、工程与地学拓展",
)

# Keep the historical storage/filter key stable while using precise language
# in the student-facing UI.  Existing URLs and SQLite rows remain compatible.
CATEGORY_DISPLAY_NAMES = {
    "油气上游业主与研究机构": "油气勘探开发运营与研究机构",
}

CATEGORY_DESCRIPTIONS = {
    "油气上游业主与研究机构": "油田、勘探开发公司及油藏、地球物理研究机构",
    "油气工程技术服务": "物探、测井、钻完井、井下作业与油气技术服务",
    "管网、炼化与综合能源": "油气管输、炼化、储气库、综合能源与转型业务",
    "自然资源、地调与地勘": "地质调查、地勘、自然资源、核地质与煤冶地质",
    "矿产资源与矿业": "矿产勘查、矿业集团、资源评价与矿山技术岗位",
    "地质工程、环境与基础设施": "工程地质、地灾、环境、水文与大型工程建设",
    "科研院所、高校与博士后": "科研院所、高校教师、博士后与科研助理岗位",
    "事业单位与人才引进": "事业单位公开招聘、专业技术岗与人才引进",
    "公务员与选调": "国考、省考、选调及资源环境相关机关岗位",
    "金融与央国企综合机会": "银行、保险、金融与央国企综合培养岗位",
    "能源、工程与地学拓展": "新能源、地热、CCUS、地学数据及相邻工程方向",
}

_LEGACY_CATEGORY_MAP = {
    "三桶油与油服": "能源、工程与地学拓展",
    "自然资源与地勘": "自然资源、地调与地勘",
    "科研院所与高校": "科研院所、高校与博士后",
    "在华外企与国际机会": "能源、工程与地学拓展",
    "金融与央国企": "金融与央国企综合机会",
}


@dataclass(frozen=True)
class EmploymentProfile:
    """A concise, display-ready explanation of an employer and opportunity."""

    category: str
    employment_path: str
    employer_type: str
    affiliation: str
    opportunity_scope: str
    role_direction: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def load_employer_registry(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the small, auditable canonical employer registry.

    This registry is deliberately not a speculative directory of every
    subsidiary.  It contains only named units that have a clear public identity
    and keeps aliases visible in version control for later correction.
    """
    registry_path = path or Path(__file__).resolve().parent.parent / "data" / "employer_registry.json"
    return [dict(item) for item in _load_employer_registry(str(registry_path))]


@lru_cache(maxsize=8)
def _load_employer_registry(path: str) -> tuple[dict[str, Any], ...]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    try:
        return tuple(validate_employer_registry(payload))
    except ContractValidationError as error:
        raise ValueError(f"employer_registry.json is invalid: {error}") from error


def resolve_employer(
    employer: str | None,
    *,
    registry_path: Path | None = None,
) -> dict[str, Any] | None:
    """Resolve an explicit employer string without guessing from a job body.

    Longest aliases win, so a specific technical-service subsidiary is not
    collapsed into a broad parent such as China National Petroleum Corporation.
    """
    identity = clean_employment_text(employer).lower()
    if not identity:
        return None
    matches: list[tuple[int, dict[str, Any]]] = []
    for item in load_employer_registry(registry_path):
        for alias in item["aliases"]:
            normalized_alias = clean_employment_text(str(alias)).lower()
            if normalized_alias and normalized_alias in identity:
                matches.append((len(normalized_alias), item))
    if not matches:
        return None
    _, best = max(matches, key=lambda item: item[0])
    return {
        "canonical_employer_id": best["id"],
        "canonical_employer_name": best["canonical_name"],
        "parent_employer_name": best.get("parent_name"),
        "category": best["category"],
        "employer_type": best["employer_type"],
        "affiliation": best["affiliation"],
    }


def clean_employment_text(value: str | None) -> str:
    """Normalize text locally to avoid a dependency cycle with matching.py."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


def classify_employment(
    text: str,
    *,
    location: str | None = None,
    source_category: str | None = None,
    identity_text: str | None = None,
) -> EmploymentProfile:
    """Classify an opportunity by industry-chain role, not by brand alone.

    Pattern order is intentional.  For example, China Petroleum BGP contains
    "China Petroleum", but it is a geophysical technical-service employer and
    must be recognised before the generic upstream-operator rule.
    """
    combined = clean_employment_text(f"{text} {location or ''}").lower()
    identity = clean_employment_text(identity_text).lower()
    scope = infer_opportunity_scope(location, combined)
    direction = infer_role_direction(combined)

    identity_category = _infer_identity_category(identity)
    normalized_source = (
        _normalize_source_category(source_category)
        if source_category in CATEGORY_ORDER or source_category in _LEGACY_CATEGORY_MAP
        else None
    )
    forced_category = identity_category or normalized_source
    if forced_category:
        return _profile_for_category(
            forced_category,
            combined,
            scope,
            direction,
            identity_text=identity,
        )

    if _contains_any(combined, _CIVIL_SERVICE_MARKERS):
        return _profile(
            "公务员与选调",
            "公共部门岗位",
            "机关招录或选调机会",
            "政府机关",
            scope,
            direction,
        )
    if _contains_any(combined, _OILFIELD_SERVICE_MARKERS):
        return _oilfield_service_profile(
            combined,
            scope,
            direction,
            identity_text=identity,
        )
    if _contains_any(combined, _PIPELINE_AND_ENERGY_MARKERS):
        return _pipeline_energy_profile(combined, scope, direction)
    if _contains_any(combined, _UPSTREAM_OPERATOR_MARKERS):
        return _upstream_profile(combined, scope, direction)
    if _contains_any(combined, _NATURAL_RESOURCE_MARKERS):
        return _profile(
            "自然资源、地调与地勘",
            "自然资源与地勘",
            "地质调查、地勘或资源管理单位",
            _natural_resource_affiliation(combined),
            scope,
            direction,
        )
    if _contains_any(combined, _MINING_MARKERS):
        return _profile(
            "矿产资源与矿业",
            "矿产资源与矿业",
            "矿业集团或矿产勘查单位",
            _mining_affiliation(combined),
            scope,
            direction,
        )
    if _contains_any(combined, _GEOTECH_MARKERS):
        return _profile(
            "地质工程、环境与基础设施",
            "工程与环境地质",
            "工程建设、环境或地质技术单位",
            _infrastructure_affiliation(combined),
            scope,
            direction,
        )
    if _contains_any(combined, _RESEARCH_MARKERS):
        return _profile(
            "科研院所、高校与博士后",
            "科研与高等教育",
            "高校、科研院所或博士后培养单位",
            "高校/科研院所",
            scope,
            direction,
        )
    if _contains_any(combined, _PUBLIC_INSTITUTION_MARKERS):
        return _profile(
            "事业单位与人才引进",
            "事业单位与人才引进",
            "事业单位专业技术岗位或人才引进",
            "事业单位/地方人才体系",
            scope,
            direction,
        )
    if _contains_any(combined, _FINANCE_MARKERS):
        return _profile(
            "金融与央国企综合机会",
            "金融与综合培养",
            "金融机构或央国企综合岗位",
            "金融/央国企",
            scope,
            direction,
        )
    if _contains_any(combined, _ENERGY_EXTENSION_MARKERS):
        return _profile(
            "能源、工程与地学拓展",
            "能源与地学拓展",
            "能源、工程或地学相邻方向单位",
            _energy_extension_affiliation(combined),
            scope,
            direction,
        )

    fallback = _normalize_source_category(source_category)
    return _profile(
        fallback,
        fallback,
        "单位属性待以官方公告核验",
        "所属体系待核验",
        scope,
        direction,
    )


def enrich_job(job: dict[str, Any]) -> dict[str, Any]:
    """Add non-persistent display attributes to a database or report job row."""
    enriched = dict(job)
    enriched["category_label"] = CATEGORY_DISPLAY_NAMES.get(
        str(job.get("category") or ""), str(job.get("category") or "")
    )
    identity_values = (job.get("title"), job.get("employer"))
    identity = resolve_employer(str(job.get("employer") or ""))
    profile = classify_employment(
        " ".join(
            str(value)
            for value in (
                *identity_values,
                job.get("group_name"),
                job.get("description"),
                job.get("summary"),
            )
            if value
        ),
        location=job.get("location"),
        source_category=job.get("category"),
        identity_text=" ".join(str(value) for value in identity_values if value),
    )
    enriched.update(profile.as_dict())
    if identity:
        # Persistent rows already carry these values after v0.3 migration.
        # Applying them here also keeps frozen reports from older versions clear.
        if not enriched.get("canonical_employer_id"):
            enriched["canonical_employer_id"] = identity["canonical_employer_id"]
        if not enriched.get("canonical_employer_name"):
            enriched["canonical_employer_name"] = identity["canonical_employer_name"]
        if not enriched.get("parent_employer_name"):
            enriched["parent_employer_name"] = identity["parent_employer_name"]
        enriched["category"] = identity["category"]
        enriched["category_label"] = CATEGORY_DISPLAY_NAMES.get(
            identity["category"], identity["category"]
        )
        enriched["employer_type"] = identity["employer_type"]
        enriched["affiliation"] = identity["affiliation"]
    return enriched


def load_employment_landscape(path: Path | None = None) -> dict[str, Any]:
    """Load the maintainable representative employer map used by the website."""
    landscape_path = path or Path(__file__).resolve().parent.parent / "data" / "employment_landscape.json"
    with landscape_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise ValueError("employment_landscape.json must contain a groups list")
    return payload


def infer_opportunity_scope(location: str | None, lowered_text: str) -> str:
    """Make cross-border status explicit without inferring visa eligibility."""
    value = clean_employment_text(location).lower() or lowered_text
    if _contains_any(value, _OVERSEAS_MARKERS) or re.search(
        r"\b(?:us|uk|gb|my|ae|sa|no|ca|au|br|mx|in|id|sr)\b", value
    ):
        return "海外岗位"
    if _contains_any(value, _DOMESTIC_LOCATION_MARKERS):
        return "中国境内岗位"
    return "地域待核验"


def infer_role_direction(lowered_text: str) -> str:
    """Return one primary direction to keep cards brief and scannable."""
    directions = (
        (("物探", "地震勘探", "geophysical", "geophysics", "seismic"), "物探与地球物理"),
        (("测井", "petrophysics", "petrophysical", "wireline", "well logging"), "测井与储层评价"),
        (("储层", "油藏", "reservoir", "geomodel", "subsurface"), "油藏与储层研究"),
        (("钻井", "钻完井", "定向井", "drilling", "completion", "perforating"), "钻完井与井工程"),
        (("矿产", "矿业", "mineral", "mining"), "矿产勘查与矿业"),
        (("遥感", "gis", "地理信息", "geospatial", "remote sensing"), "遥感、GIS 与地学数据"),
        (("工程地质", "水文地质", "环境地质", "地质灾害", "environmental"), "工程与环境地质"),
        (("地质", "geology", "geoscience", "资源勘查", "地球科学"), "地质与资源评价"),
    )
    for markers, direction in directions:
        if _contains_any(lowered_text, markers):
            return direction
    return "方向待核验"


def _profile(
    category: str,
    employment_path: str,
    employer_type: str,
    affiliation: str,
    opportunity_scope: str,
    role_direction: str,
) -> EmploymentProfile:
    return EmploymentProfile(
        category=category,
        employment_path=employment_path,
        employer_type=employer_type,
        affiliation=affiliation,
        opportunity_scope=opportunity_scope,
        role_direction=role_direction,
    )


def _oilfield_service_profile(
    value: str,
    scope: str,
    direction: str,
    *,
    identity_text: str = "",
) -> EmploymentProfile:
    if _contains_any(value, ("东方物探", "bgp", "地球物理勘探")):
        employer_type = "油气地球物理技术服务企业"
    elif _contains_any(value, ("测井", "well logging", "wireline")):
        employer_type = "测井、储层评价与井下技术服务企业"
    elif _contains_any(value, ("钻探", "钻井", "固井", "定向井", "drilling")):
        employer_type = "钻完井与井工程技术服务企业"
    else:
        employer_type = "油气工程技术服务企业"
    affiliation = _oilfield_service_affiliation(identity_text)
    if affiliation == "油气技术服务体系待核验":
        # Announcements can mention partner operators or customers. Use those
        # terms only when the title/employer gave us no identity clue.
        affiliation = _oilfield_service_affiliation(value)
    return _profile(
        "油气工程技术服务",
        "油气工程技术服务",
        employer_type,
        affiliation,
        scope,
        direction,
    )


def _profile_for_category(
    category: str,
    value: str,
    scope: str,
    direction: str,
    *,
    identity_text: str = "",
) -> EmploymentProfile:
    """Build a display profile after high-confidence identity classification."""
    if category == "油气工程技术服务":
        return _oilfield_service_profile(
            value,
            scope,
            direction,
            identity_text=identity_text,
        )
    if category == "油气上游业主与研究机构":
        return _upstream_profile(
            value,
            scope,
            direction,
            identity_text=identity_text,
        )
    if category == "管网、炼化与综合能源":
        return _pipeline_energy_profile(
            value,
            scope,
            direction,
            identity_text=identity_text,
        )
    if category == "自然资源、地调与地勘":
        return _profile(
            category,
            "自然资源与地勘",
            "地质调查、地勘或资源管理单位",
            _identity_first_affiliation(
                identity_text,
                value,
                _natural_resource_affiliation,
                ("自然资源/地勘体系",),
            ),
            scope,
            direction,
        )
    if category == "矿产资源与矿业":
        return _profile(
            category,
            "矿产资源与矿业",
            "矿业集团或矿产勘查单位",
            _identity_first_affiliation(
                identity_text,
                value,
                _mining_affiliation,
                ("矿业资源体系",),
            ),
            scope,
            direction,
        )
    if category == "地质工程、环境与基础设施":
        return _profile(
            category,
            "工程与环境地质",
            "工程建设、环境或地质技术单位",
            _identity_first_affiliation(
                identity_text,
                value,
                _infrastructure_affiliation,
                ("工程/环境技术体系",),
            ),
            scope,
            direction,
        )
    if category == "科研院所、高校与博士后":
        return _profile(
            category,
            "科研与高等教育",
            "高校、科研院所或博士后培养单位",
            "高校/科研院所",
            scope,
            direction,
        )
    if category == "事业单位与人才引进":
        return _profile(
            category,
            "事业单位与人才引进",
            "事业单位专业技术岗位或人才引进",
            "事业单位/地方人才体系",
            scope,
            direction,
        )
    if category == "公务员与选调":
        return _profile(
            category,
            "公共部门岗位",
            "机关招录或选调机会",
            "政府机关",
            scope,
            direction,
        )
    if category == "金融与央国企综合机会":
        return _profile(
            category,
            "金融与综合培养",
            "金融机构或央国企综合岗位",
            "金融/央国企",
            scope,
            direction,
        )
    if category == "能源、工程与地学拓展":
        return _profile(
            category,
            "能源与地学拓展",
            "能源、工程或地学相邻方向单位",
            _identity_first_affiliation(
                identity_text,
                value,
                _energy_extension_affiliation,
                ("能源与工程体系",),
            ),
            scope,
            direction,
        )
    return _profile(
        category,
        category,
        "单位属性待以官方公告核验",
        "所属体系待核验",
        scope,
        direction,
    )


def _infer_identity_category(identity: str) -> str | None:
    """Infer a category from title/employer only, avoiding body-text noise."""
    if not identity:
        return None
    checks = (
        ("油气工程技术服务", _OILFIELD_SERVICE_MARKERS),
        ("公务员与选调", _CIVIL_SERVICE_MARKERS),
        ("金融与央国企综合机会", _FINANCE_MARKERS),
        (
            "管网、炼化与综合能源",
            (
                "国家管网",
                "管输",
                "油气管网",
                "管道",
                "储气库",
                "炼化",
                "炼油",
                "石油化工",
                "pipeline",
                "refining",
                "petrochemical",
            ),
        ),
        ("油气上游业主与研究机构", _UPSTREAM_OPERATOR_MARKERS),
        ("矿产资源与矿业", _MINING_MARKERS),
        ("地质工程、环境与基础设施", _GEOTECH_MARKERS),
        (
            "科研院所、高校与博士后",
            (
                "中国科学院",
                "中国地质科学院",
                "研究所",
                "研究院",
                "博士后",
                "科研助理",
                "大学",
                "学院",
            ),
        ),
        (
            "自然资源、地调与地勘",
            (
                "中国地质调查局",
                "中国煤炭地质",
                "中国冶金地质",
                "自然资源部",
                "地勘局",
                "地质调查院",
                "地质矿产",
                "核工业",
                "煤炭地质",
                "冶金地质",
            ),
        ),
        ("能源、工程与地学拓展", _ENERGY_EXTENSION_MARKERS),
    )
    for category, markers in checks:
        if _contains_any(identity, markers):
            return category
    return None


def _identity_first_affiliation(
    identity: str,
    value: str,
    resolver: Any,
    unresolved: tuple[str, ...],
) -> str:
    """Resolve employer affiliation from identity before announcement prose."""
    if identity:
        candidate = resolver(identity)
        if candidate not in unresolved:
            return candidate
    return resolver(value)


def _upstream_profile(
    value: str,
    scope: str,
    direction: str,
    *,
    identity_text: str = "",
) -> EmploymentProfile:
    employer_type = (
        "油气上游业主/运营主体"
        if not _contains_any(value, ("研究院", "研究所", "技术中心"))
        else "油气勘探开发研究机构"
    )
    affiliation = _identity_first_affiliation(
        identity_text,
        value,
        _upstream_affiliation,
        ("上游能源体系待核验",),
    )
    return _profile(
        "油气上游业主与研究机构",
        "油气上游勘探开发",
        employer_type,
        affiliation,
        scope,
        direction,
    )


def _pipeline_energy_profile(
    value: str,
    scope: str,
    direction: str,
    *,
    identity_text: str = "",
) -> EmploymentProfile:
    if _contains_any(value, ("国家管网", "管输", "管道", "储气库", "pipeline")):
        employer_type = "油气管网与储运运营单位"
        affiliation = "国家管网/管输体系"
    elif _contains_any(value, ("炼化", "炼油", "化工", "refining", "petrochemical")):
        employer_type = "炼化与化工一体化单位"
        affiliation = "能源央企/大型能源集团"
    else:
        employer_type = "综合能源与能源转型单位"
        affiliation = _identity_first_affiliation(
            identity_text,
            value,
            _energy_extension_affiliation,
            ("能源与工程体系",),
        )
    return _profile(
        "管网、炼化与综合能源",
        "管网、炼化与综合能源",
        employer_type,
        affiliation,
        scope,
        direction,
    )


def _oilfield_service_affiliation(value: str) -> str:
    marker_groups = (
        (
            "中国海油体系",
            ("中海油服", "中海油能源发展", "cosl", "cnooc"),
        ),
        (
            "中国石化体系",
            ("石化油服", "中石化", "中国石化", "sinopec"),
        ),
        (
            "中国石油体系",
            (
                "东方物探",
                "bgp",
                "中石油",
                "中国石油",
                "cnpc",
                "川庆",
                "长城钻探",
                "渤海钻探",
                "西部钻探",
            ),
        ),
        (
            "国际企业",
            (
                "slb",
                "斯伦贝谢",
                "halliburton",
                "哈里伯顿",
                "baker hughes",
                "贝克休斯",
                "weatherford",
                "威德福",
            ),
        ),
        ("独立/民营油服", ("杰瑞", "安东", "海隆", "中曼", "贝肯", "通源")),
    )
    matches = [
        (position, affiliation)
        for affiliation, markers in marker_groups
        for marker in markers
        for position in [value.find(marker)]
        if position >= 0
    ]
    if matches:
        return min(matches, key=lambda item: item[0])[1]
    return "油气技术服务体系待核验"


def _upstream_affiliation(value: str) -> str:
    if _contains_any(value, ("中石化", "中国石化", "sinopec")):
        return "中国石化体系"
    if _contains_any(value, ("中海油", "中国海油", "cnooc")):
        return "中国海油体系"
    if _contains_any(value, ("中石油", "中国石油", "cnpc", "petrochina")):
        return "中国石油体系"
    if _contains_any(value, ("shell", "壳牌", "exxon", "埃克森", "totalenergies", "道达尔", "chevron", "雪佛龙")):
        return "国际能源企业"
    return "上游能源体系待核验"


def _natural_resource_affiliation(value: str) -> str:
    if _contains_any(value, ("中国地质调查局", "中国地质科学院", "自然资源部")):
        return "自然资源部系统"
    if _contains_any(value, ("核工业", "中核", "cnnc")):
        return "核工业地质体系"
    if _contains_any(value, ("煤炭地质", "冶金地质")):
        return "中央地勘单位"
    if _contains_any(value, ("地质局", "地勘局", "地质调查院", "地矿")):
        return "地方地勘体系"
    return "自然资源/地勘体系"


def _mining_affiliation(value: str) -> str:
    if _contains_any(value, ("五矿", "中国黄金", "中铝", "minmetals", "chinalco")):
        return "中央资源企业"
    if _contains_any(value, ("紫金", "山东黄金", "招金")):
        return "大型矿业集团"
    return "矿业资源体系"


def _infrastructure_affiliation(value: str) -> str:
    if _contains_any(value, ("中国电建", "powerchina", "中国能建", "ceec")):
        return "能源建设央企"
    if _contains_any(value, ("中交", "中国铁建", "中国中铁", "crcc")):
        return "基础设施央企"
    return "工程/环境技术体系"


def _energy_extension_affiliation(value: str) -> str:
    if _contains_any(value, ("国家能源", "国电投", "华能", "华电", "大唐")):
        return "能源央企"
    if _contains_any(value, ("slb", "halliburton", "baker hughes", "weatherford")):
        return "国际企业"
    return "能源与工程体系"


def _normalize_source_category(value: str | None) -> str:
    if value in CATEGORY_ORDER:
        return str(value)
    return _LEGACY_CATEGORY_MAP.get(str(value), "能源、工程与地学拓展")


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


_OILFIELD_SERVICE_MARKERS = (
    "东方物探", "bgp", "中海油服", "cosl", "石化油服", "石油工程技术服务",
    "中国石化石油工程技术服务", "中石化石油工程", "中国石油集团测井",
    "中国石油集团钻井", "川庆钻探", "长城钻探", "渤海钻探", "西部钻探",
    "杰瑞", "jereh", "安东", "anton oil", "海隆", "hilong", "中曼", "zpec",
    "贝肯", "通源", "slb", "斯伦贝谢", "halliburton", "哈里伯顿",
    "baker hughes", "贝克休斯", "weatherford", "威德福", "油田服务",
    "oilfield service", "oilfield services",
)

_UPSTREAM_OPERATOR_MARKERS = (
    "中国石油", "中石油", "cnpc", "petrochina", "中国石化", "中石化", "sinopec",
    "中国海油", "中海油", "cnooc", "油田", "勘探开发", "exploration and production",
    "e&p operator", "shell", "壳牌", "exxon", "埃克森", "totalenergies", "道达尔",
    "chevron", "雪佛龙",
)

_PIPELINE_AND_ENERGY_MARKERS = (
    "国家管网", "管输", "油气管网", "管道", "储气库", "pipeline", "炼化", "炼油",
    "石油化工", "refining", "petrochemical", "综合能源", "氢能", "储能",
)

_NATURAL_RESOURCE_MARKERS = (
    "中国地质调查局", "中国地质科学院", "自然资源部", "地质调查", "地质局", "地勘局",
    "地质调查院", "地矿", "地勘", "核地质", "核工业", "煤炭地质", "冶金地质",
)

_MINING_MARKERS = (
    "矿产", "矿业", "矿山", "中国五矿", "五矿", "中国黄金", "紫金矿业", "山东黄金",
    "中铝", "minmetals", "mining", "mineral exploration",
)

_GEOTECH_MARKERS = (
    "工程地质", "水文地质", "环境地质", "地质灾害", "岩土", "生态环境", "中国电建",
    "中国能建", "中国中铁", "中国铁建", "中交", "powerchina", "ceec", "crcc",
)

_RESEARCH_MARKERS = (
    "博士后", "科研助理", "研究员", "副研究员", "讲师", "教师", "大学", "学院",
    "研究所", "研究院", "中国科学院", "高校",
)

_PUBLIC_INSTITUTION_MARKERS = (
    "事业单位", "人才引进", "公开招聘", "专业技术岗", "专业技术岗位",
)

_CIVIL_SERVICE_MARKERS = (
    "公务员", "国考", "省考", "选调生", "选调", "机关招录",
)

_FINANCE_MARKERS = (
    "银行", "保险", "证券", "基金", "金融", "国家开发银行", "进出口银行",
)

_ENERGY_EXTENSION_MARKERS = (
    "新能源", "地热", "ccus", "碳捕集", "碳封存", "碳储存", "地学数据", "能源",
    "geothermal", "carbon storage", "renewable energy",
)

_OVERSEAS_MARKERS = (
    "海外", "境外", "马来西亚", "美国", "英国", "挪威", "阿联酋", "沙特", "加拿大",
    "澳大利亚", "巴西", "苏里南", "印度尼西亚", "malaysia", "houston", "aberdeen",
    "paramaribo", "dubai", "norway", "canada", "australia", "brazil", "suriname",
)

_DOMESTIC_LOCATION_MARKERS = (
    "中国", "北京", "上海", "天津", "重庆", "河北", "山西", "内蒙古", "辽宁", "吉林",
    "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "广西", "海南", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海",
    "宁夏", "新疆", "香港", "澳门", "台湾", "克拉玛依", "东营", "大庆", "成都", "涿州",
)
