from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from job_hub.matching import clean_text


DEGREE_ORDER = ("本科", "硕士", "博士")
DEGREE_RANK = {degree: index for index, degree in enumerate(DEGREE_ORDER)}


@dataclass(frozen=True)
class StudentProfile:
    """A public, non-identifying study profile supported by this site."""

    id: str
    degree: str
    major: str
    exact_major_terms: tuple[str, ...]
    english_exact_major_terms: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.degree} · {self.major}"


@dataclass(frozen=True)
class ProfileMatch:
    level: str
    label: str
    reason: str
    priority: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# These seven profiles mirror the programs specified for CUPB Geoscience School.
# The aliases only make an announcement easier to recognize; they never create a
# claim that a student is eligible when the original notice says otherwise.
STUDENT_PROFILES = (
    StudentProfile(
        id="undergraduate-resource-exploration",
        degree="本科",
        major="资源勘查工程",
        exact_major_terms=("资源勘查工程", "资源勘探工程", "资源勘探", "勘查技术与工程", "矿产普查与勘探"),
        english_exact_major_terms=("resource exploration engineering", "mineral exploration"),
    ),
    StudentProfile(
        id="master-geology",
        degree="硕士",
        major="地质学",
        exact_major_terms=("地质学",),
        english_exact_major_terms=("geology", "geological science", "geological sciences"),
    ),
    StudentProfile(
        id="master-geological-engineering",
        degree="硕士",
        major="地质工程",
        exact_major_terms=("地质工程",),
        english_exact_major_terms=("geological engineering",),
    ),
    StudentProfile(
        id="master-geological-resources-engineering",
        degree="硕士",
        major="地质资源与地质工程",
        exact_major_terms=("地质资源与地质工程", "地质资源"),
        english_exact_major_terms=("geological resources and engineering",),
    ),
    StudentProfile(
        id="doctoral-geology",
        degree="博士",
        major="地质学",
        exact_major_terms=("地质学",),
        english_exact_major_terms=("geology", "geological science", "geological sciences"),
    ),
    StudentProfile(
        id="doctoral-geological-engineering",
        degree="博士",
        major="地质工程",
        exact_major_terms=("地质工程",),
        english_exact_major_terms=("geological engineering",),
    ),
    StudentProfile(
        id="doctoral-geological-resources-engineering",
        degree="博士",
        major="地质资源与地质工程",
        exact_major_terms=("地质资源与地质工程", "地质资源"),
        english_exact_major_terms=("geological resources and engineering",),
    ),
)


def list_student_profiles() -> tuple[StudentProfile, ...]:
    return STUDENT_PROFILES


def get_student_profile(profile_id: str | None) -> StudentProfile | None:
    if not profile_id:
        return None
    return next((item for item in STUDENT_PROFILES if item.id == profile_id), None)


def evaluate_profile_match(
    job: dict[str, Any],
    profile: StudentProfile,
) -> ProfileMatch:
    """Return an explainable recommendation, never an eligibility guarantee."""
    major_status, major_reason = _major_status(job, profile)
    degree_status, degree_reason = _degree_status(job, profile)

    if degree_status == "blocked":
        return ProfileMatch(
            level="not_recommended",
            label="学历不匹配",
            reason=degree_reason,
            priority=0,
        )

    if major_status == "explicit" and degree_status == "explicit":
        return ProfileMatch(
            level="explicit",
            label="明确匹配",
            reason=f"{major_reason}；{degree_reason}",
            priority=2,
        )

    if major_status in {"explicit", "related"}:
        return ProfileMatch(
            level="review",
            label="需核验原公告",
            reason=f"{major_reason}；{degree_reason}",
            priority=1,
        )

    if _is_domain_aligned(job):
        return ProfileMatch(
            level="review",
            label="需核验原公告",
            reason=f"岗位属于地学或油气相关方向，但{major_reason}；{degree_reason}",
            priority=1,
        )

    return ProfileMatch(
        level="not_recommended",
        label="未见直接匹配",
        reason=f"{major_reason}；{degree_reason}",
        priority=0,
    )


def annotate_profile_match(
    job: dict[str, Any],
    profile: StudentProfile | None,
) -> dict[str, Any]:
    """Copy a job record and attach display-only profile matching metadata."""
    annotated = dict(job)
    if profile is not None:
        annotated["profile_match"] = evaluate_profile_match(job, profile).as_dict()
    return annotated


def _major_status(
    job: dict[str, Any],
    profile: StudentProfile,
) -> tuple[str, str]:
    tags = [clean_text(str(item)) for item in job.get("major_tags", []) if item]
    lowered_tags = {tag.lower() for tag in tags}
    exact = [
        term
        for term in profile.exact_major_terms
        if term.lower() in lowered_tags
    ]
    if exact:
        return "explicit", f"专业范围明确包含“{exact[0]}”"
    explicit_english = [
        term
        for term in profile.english_exact_major_terms
        if _english_major_requirement_is_explicit(job, term)
    ]
    if explicit_english:
        return "explicit", f"公告学历条件明确包含“{explicit_english[0]}”"
    if tags:
        return "related", "公告列出了地学或油气相邻专业，需核对专业范围"
    return "unspecified", "公告未明确列出目标专业"


def _english_major_requirement_is_explicit(
    job: dict[str, Any],
    major_term: str,
) -> bool:
    """Require degree-context evidence before translating an English discipline.

    A job title such as ``Geologist`` or a responsibility paragraph mentioning
    geology is useful for discovery, but it does not prove the applicant's
    major requirement.  Only a nearby degree, major, discipline, or field-of-
    study condition can upgrade an English term to an explicit profile match.
    """
    source_text = clean_text(
        " ".join(
            str(job.get(field, ""))
            for field in ("title", "summary", "description")
        )
    ).lower()
    term_pattern = re.escape(major_term).replace(r"\ ", r"\s+")
    degree_context = (
        r"(?:bachelor(?:'s)?|master(?:'s)?|ph\.?d\.?|doctoral|"
        r"degree|major|discipline|field\s+of\s+study|qualification|"
        r"academic\s+background)"
    )
    return bool(
        re.search(
            rf"\b{degree_context}\b[^.!?;]{{0,100}}\b{term_pattern}\b",
            source_text,
            re.IGNORECASE,
        )
        or re.search(
            rf"\b{term_pattern}\b[^.!?;]{{0,100}}\b{degree_context}\b",
            source_text,
            re.IGNORECASE,
        )
    )


def _degree_status(
    job: dict[str, Any],
    profile: StudentProfile,
) -> tuple[str, str]:
    listed = [
        str(item)
        for item in job.get("degree_levels", [])
        if str(item) in DEGREE_RANK
    ]
    if profile.degree in listed:
        return "explicit", f"学历要求明确列出“{profile.degree}”"

    source_text = clean_text(
        " ".join(
            str(job.get(field, ""))
            for field in ("title", "summary", "description")
        )
    ).lower()
    if _accepts_degree_or_above(source_text, profile.degree):
        return "explicit", f"公告写明“{profile.degree}及以上”或更低学历及以上"

    if not listed:
        return "unspecified", "公告未注明学历要求"

    profile_rank = DEGREE_RANK[profile.degree]
    listed_ranks = [DEGREE_RANK[item] for item in listed]
    if profile_rank < min(listed_ranks):
        expected = " / ".join(listed)
        return "blocked", f"公告仅面向“{expected}”，不包含“{profile.degree}”"

    expected = " / ".join(listed)
    return "review", f"公告列出“{expected}”，请核对是否接受“{profile.degree}”"


def _accepts_degree_or_above(text: str, profile_degree: str) -> bool:
    """Recognize common degree-floor wording without guessing unlisted eligibility."""
    variants = {
        "本科": ("本科", "学士", "bachelor", "undergraduate"),
        "硕士": ("硕士", "master"),
        "博士": ("博士", "ph.d", "phd", "doctoral"),
    }
    profile_rank = DEGREE_RANK[profile_degree]
    for degree, words in variants.items():
        if DEGREE_RANK[degree] > profile_rank:
            continue
        for word in words:
            if word in text and any(
                phrase in text
                for phrase in (
                    f"{word}及以上",
                    f"{word}学历及以上",
                    f"{word}学位及以上",
                    f"{word}'s degree or above",
                    f"{word} degree or above",
                )
            ):
                return True
    return False


def _is_domain_aligned(job: dict[str, Any]) -> bool:
    return int(job.get("relevance_score", 0)) >= 55
