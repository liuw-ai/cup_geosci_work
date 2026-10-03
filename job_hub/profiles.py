from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from job_hub.matching import (
    clean_text,
    extract_degree_levels,
    has_major_qualification_evidence,
    qualification_evidence_text,
)
from job_hub.major_taxonomy import (
    bounded_exact_terms_for_profile,
    english_exact_terms_for_profile,
    exact_terms_for_profile,
    category_terms_for_profile,
    profile_major_definition,
    related_terms_for_profile,
)
from job_hub.locations import MAINLAND_COUNTRY_LABELS


DEGREE_ORDER = ("大专", "本科", "硕士", "博士")
DEGREE_RANK = {degree: index for index, degree in enumerate(DEGREE_ORDER)}

# These values answer a different question from ``relevance_band``.  The
# latter remains useful for internal source triage; this status is the one
# and only gate for what a Geoscience School student can see publicly.
PUBLICATION_STUDENT_ELIGIBLE = "student_eligible"
PUBLICATION_UNRESTRICTED_ELIGIBLE = "unrestricted_eligible"
PUBLICATION_PENDING_EVIDENCE = "pending_evidence"
PUBLICATION_OUT_OF_SCOPE = "out_of_scope"
PUBLIC_STUDENT_PUBLICATION_STATUSES = frozenset(
    {
        PUBLICATION_STUDENT_ELIGIBLE,
        PUBLICATION_UNRESTRICTED_ELIGIBLE,
    }
)

DEGREE_EVIDENCE_KEYS = frozenset(
    {
        "学历要求",
        "学历",
        "学历层次",
        "学位要求",
        "学历及学位",
        "学历学位",
        "学历条件",
        "最低学历",
        "学位",
        "面向对象",
        "education",
        "degree",
        "degree requirement",
        "education requirement",
        "minimum education",
        # These are accepted only when the source has already established
        # that the text belongs to one specific job row/detail block.
        "岗位要求",
        "任职要求",
        "资格条件",
        "应聘条件",
        "job requirements",
        "job requirement",
        "qualifications",
    }
)
ELIGIBILITY_EVIDENCE_KEYS = frozenset(
    {
        # Some official portals expose the whole requirement block in their
        # degree field (for example, ``学历要求`` may also contain a minimum
        # years-of-experience clause).  Read the degree-labelled fields for
        # the student-only experience veto as well; major matching remains
        # restricted to ``qualification_evidence_text`` below.
        "学历要求",
        "学历",
        "学历层次",
        "学位要求",
        "学历及学位",
        "学历学位",
        "学历条件",
        "最低学历",
        "学位",
        "education",
        "degree",
        "degree requirement",
        "education requirement",
        "minimum education",
        "岗位要求",
        "任职要求",
        "任职条件",
        "任职资格",
        "资格条件",
        "招聘条件",
        "应聘条件",
        "岗位条件",
        "岗位资格条件",
        "工作经验",
        "工作年限",
        "面向对象",
        "job requirements",
        "job requirement",
        "qualifications",
        "experience",
    }
)
# ``row_text`` is intentionally excluded from major matching because an
# attachment row can contain unrelated text alongside its degree/major cells.
# It is safe for the narrower experience veto only when the collector already
# proves that it is one official attachment row for the displayed position.
ATTACHMENT_ROW_EVIDENCE_KEYS = frozenset({"row_text", "岗位行原文"})
UNRESTRICTED_MAJOR_MARKERS = (
    "不限专业",
    "专业不限",
    "专业不作限制",
    "不限学科",
    "any major",
    "all majors",
)

# The public service is for current CUPB Geoscience students. A role can name
# one of their majors and still be unsuitable when it explicitly requires a
# multi-year employment record. This is deliberately narrow: it triggers only
# on an unambiguous work-experience condition in the same official job block.
EXPERIENCE_REQUIREMENT_PATTERNS = (
    re.compile(
        r"(?:具有|具备|需|要求|不少于|至少|满)?\s*"
        r"(?:[一二三四五六七八九十百千\d]+)\s*年(?:以上|以内)?"
        r"[^。；;\n]{0,24}?(?:工作|从业|项目|行业|工程|勘察|设计)(?:经验|经历)"
    ),
    re.compile(
        r"(?:工作|从业|项目|行业|工程|勘察|设计)(?:经验|经历)\s*"
        r"(?:不少于|至少)?\s*(?:[一二三四五六七八九十百千\d]+)\s*年"
    ),
    re.compile(
        r"(?:at least|minimum(?: of)?|over)?\s*\d+\+?\s*years?"
        r"(?:\s+of\s+[^.;\n]{0,80})?\s+experience",
        re.I,
    ),
)
FRESH_GRADUATE_EXEMPTION_PATTERNS = (
    re.compile(
        r"(?:优秀)?应届(?:毕业生)?(?:也)?(?:可|可以|均可|欢迎)?"
        r"(?:投递|报名|应聘|申请|参加|报考)"
    ),
    re.compile(
        r"fresh\s+graduates?(?:\s+are)?\s+(?:welcome|eligible|encouraged)"
        r"|fresh\s+graduates?\s+may\s+apply",
        re.I,
    ),
)

# A major or degree phrase from a whole announcement can belong to another
# role.  Public records therefore need a collector-provided locator that
# binds its structured evidence to the displayed job title.
JOB_LEVEL_EVIDENCE_SCOPES = frozenset(
    {
        "official_html_table_row",
        "official_attachment_row",
        "official_role_section",
        "official_detail_block",
        # Browser workers bind the extracted fields to the same official
        # detail/list row. Keep these scopes in the same publication contract
        # as HTML and attachment rows; otherwise a valid browser capture is
        # silently downgraded to ``pending_evidence``.
        "official_browser_capture_row",
        "official_cnpc_browser_job_row",
        # The Sinopec SPA is captured from each official enterprise detail
        # route and then replayed from a versioned, administrator-verified
        # snapshot.  It is still job-level evidence: the capture stores the
        # detail title, major field and degree field for the same row.
        "official_sinopec_detail_snapshot",
        # A server-side Playwright capture of the public GuoPin detail route.
        # The capture manifest binds the title, major, degree, location,
        # headcount and deadline to the same official detail URL.
        "official_cmgb_browser_detail",
        # Other employer-owned GuoPin portals use the same complete-detail
        # contract, but retain their own source scope for traceability.
        "official_iguopin_browser_detail",
        # CNOOC's Zhaopin detail page exposes the same job-level fields in
        # window.__INITIAL_DATA__; the browser manifest preserves that URL and
        # title binding before this scope reaches the publication gate.
        "official_cnooc_browser_detail",
        "official_zhaopin_detail_initial_data",
        "admin_verified_official_record",
    }
)
JOB_TITLE_EVIDENCE_KEYS = frozenset(
    {
        "岗位",
        "岗位名称",
        "招聘岗位",
        "职位",
        "职位名称",
        "job title",
        "title",
    }
)


@dataclass(frozen=True)
class StudentProfile:
    """A public, non-identifying study profile supported by this site."""

    id: str
    degree: str
    major: str
    exact_major_terms: tuple[str, ...]
    bounded_exact_major_terms: tuple[str, ...] = ()
    category_major_terms: tuple[str, ...] = ()
    english_exact_major_terms: tuple[str, ...] = ()
    related_major_terms: tuple[str, ...] = ()
    taxonomy_id: str = ""

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


@dataclass(frozen=True)
class PublicationDecision:
    """Student-facing publication decision backed by official row evidence."""

    status: str
    label: str
    reason: str
    matched_profile_ids: tuple[str, ...]

    @property
    def is_public(self) -> bool:
        return self.status in PUBLIC_STUDENT_PUBLICATION_STATUSES

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_profile_ids"] = list(self.matched_profile_ids)
        return payload


# These ten profiles mirror the programs specified for CUPB Geoscience School.
# The aliases only make an announcement easier to recognize; they never create a
# claim that a student is eligible when the original notice says otherwise.
def _profile(profile_id: str, degree: str, major: str) -> StudentProfile:
    """Build a profile from the versioned taxonomy instead of local aliases."""
    return StudentProfile(
        id=profile_id,
        degree=degree,
        major=major,
        exact_major_terms=exact_terms_for_profile(profile_id),
        bounded_exact_major_terms=bounded_exact_terms_for_profile(profile_id),
        category_major_terms=category_terms_for_profile(profile_id),
        english_exact_major_terms=english_exact_terms_for_profile(profile_id),
        related_major_terms=related_terms_for_profile(profile_id),
        taxonomy_id=profile_major_definition(profile_id).id,
    )


STUDENT_PROFILES = (
    _profile("undergraduate-resource-exploration", "本科", "资源勘查工程"),
    _profile("undergraduate-geophysics", "本科", "地球物理学"),
    _profile("master-geology", "硕士", "地质学"),
    _profile("master-geophysics", "硕士", "地球物理学"),
    _profile("master-geological-engineering", "硕士", "地质工程"),
    _profile("master-geological-resources-engineering", "硕士", "地质资源与地质工程"),
    _profile("doctoral-geology", "博士", "地质学"),
    _profile("doctoral-geophysics", "博士", "地球物理学"),
    _profile("doctoral-geological-engineering", "博士", "地质工程"),
    _profile("doctoral-geological-resources-engineering", "博士", "地质资源与地质工程"),
)


def list_student_profiles() -> tuple[StudentProfile, ...]:
    return STUDENT_PROFILES


def get_student_profile(profile_id: str | None) -> StudentProfile | None:
    if not profile_id:
        return None
    return next((item for item in STUDENT_PROFILES if item.id == profile_id), None)


def evaluate_student_publication(job: dict[str, Any]) -> PublicationDecision:
    """Decide whether an official job belongs in the public student corpus.

    Employer industry, announcement prose and historical keyword tags cannot
    publish a job.  The decision intentionally accepts only an explicit target
    major or an explicit ``unrestricted major`` statement, together with an
    official degree field that covers at least one supported student profile.
    """
    if not _has_job_level_evidence(job):
        return PublicationDecision(
            status=PUBLICATION_PENDING_EVIDENCE,
            label="待补岗位级证据",
            reason="官方记录未提供能与当前岗位对应的专业、学历字段定位。",
            matched_profile_ids=(),
        )

    qualification_text = _qualification_text(job)
    degree_text = _degree_evidence_text(job)
    if not qualification_text or not degree_text:
        missing = "专业要求" if not qualification_text else "学历要求"
        return PublicationDecision(
            status=PUBLICATION_PENDING_EVIDENCE,
            label="待补岗位级证据",
            reason=f"官方岗位行未提供可核验的{missing}字段。",
            matched_profile_ids=(),
        )
    if not extract_degree_levels(degree_text):
        return PublicationDecision(
            status=PUBLICATION_PENDING_EVIDENCE,
            label="待补岗位级证据",
            reason="官方岗位行未提供可识别的学历层次。",
            matched_profile_ids=(),
        )

    location_scope = _domestic_location_scope_decision(job)
    if location_scope is not None:
        return location_scope

    required_experience = _student_blocking_work_experience(
        _eligibility_evidence_text(job)
    )
    if required_experience:
        return PublicationDecision(
            status=PUBLICATION_OUT_OF_SCOPE,
            label="需工作经验",
            reason=(
                "官方岗位级条件明确要求"
                f"{required_experience}，不作为在校毕业生岗位发布。"
            ),
            matched_profile_ids=(),
        )

    eligible_profiles = tuple(
        profile.id
        for profile in STUDENT_PROFILES
        if _profile_is_explicitly_eligible(job, profile, qualification_text)
    )
    if eligible_profiles:
        # Government notices are often announcement-level pages with several
        # institutions and rows. Only a role already proven to match this
        # service may remain private while its row-level location is completed.
        # Checking location earlier would turn an unrelated role into a false
        # “pending location” candidate.
        if _government_record_requires_location(job) and not _location_evidence_text(job):
            return PublicationDecision(
                status=PUBLICATION_PENDING_EVIDENCE,
                label="待补岗位地点",
                reason="事业单位/公务员目标专业岗位缺少与当前岗位行绑定的工作地点证据。",
                matched_profile_ids=eligible_profiles,
            )
        return PublicationDecision(
            status=PUBLICATION_STUDENT_ELIGIBLE,
            label="目标专业明确匹配",
            reason="官方岗位级专业与学历要求覆盖本院培养方向。",
            matched_profile_ids=eligible_profiles,
        )

    if _is_unrestricted_major(qualification_text):
        degree_profiles = tuple(
            profile.id
            for profile in STUDENT_PROFILES
            if _degree_status(job, profile)[0] == "explicit"
        )
        if degree_profiles:
            if _government_record_requires_location(job) and not _location_evidence_text(job):
                return PublicationDecision(
                    status=PUBLICATION_PENDING_EVIDENCE,
                    label="待补岗位地点",
                    reason="事业单位/公务员不限专业岗位缺少与当前岗位行绑定的工作地点证据。",
                    matched_profile_ids=degree_profiles,
                )
            return PublicationDecision(
                status=PUBLICATION_UNRESTRICTED_ELIGIBLE,
                label="不限专业可报",
                reason="官方岗位级专业要求明确为不限专业，学历条件覆盖本院学生。",
                matched_profile_ids=degree_profiles,
            )

    if _has_ambiguous_related_major_requirement(qualification_text):
        return PublicationDecision(
            status=PUBLICATION_PENDING_EVIDENCE,
            label="待补岗位级证据",
            reason="官方岗位行仅说明相关专业，未直接列出本院目标专业。",
            matched_profile_ids=(),
        )

    return PublicationDecision(
        status=PUBLICATION_OUT_OF_SCOPE,
        label="专业不匹配",
        reason="官方岗位级专业要求未覆盖本院目标专业。",
        matched_profile_ids=(),
    )


def _domestic_location_scope_decision(
    job: dict[str, Any],
) -> PublicationDecision | None:
    """Keep non-mainland or unclassified pipeline rows out of public results."""

    # Direct unit tests can intentionally omit normalised location fields.
    # Pipeline rows always have country_or_region from normalize_location.
    if "country_or_region" not in job:
        return None
    country = str(job.get("country_or_region") or "").strip()
    if country in MAINLAND_COUNTRY_LABELS:
        return None
    if not country:
        return PublicationDecision(
            status=PUBLICATION_PENDING_EVIDENCE,
            label="待补岗位地点",
            reason="官方岗位地点尚未能确认属于中国大陆，不作为国内岗位公开。",
            matched_profile_ids=(),
        )
    return PublicationDecision(
        status=PUBLICATION_OUT_OF_SCOPE,
        label="非中国大陆岗位",
        reason=f"官方岗位地点标明为“{country}”，不纳入国内岗位库。",
        matched_profile_ids=(),
    )


def evaluate_profile_match(
    job: dict[str, Any],
    profile: StudentProfile,
) -> ProfileMatch:
    """Return an explainable recommendation, never an eligibility guarantee."""
    publication = evaluate_student_publication(job)
    if (
        publication.status == PUBLICATION_STUDENT_ELIGIBLE
        and profile.id in publication.matched_profile_ids
    ):
        term = _matched_profile_term(profile, _qualification_text(job))
        return ProfileMatch(
            level="explicit",
            label="明确匹配",
            reason=(
                f"专业范围明确包含“{term}”；{_degree_status(job, profile)[1]}"
            ),
            priority=2,
        )
    if publication.status == PUBLICATION_UNRESTRICTED_ELIGIBLE and profile.id in publication.matched_profile_ids:
        return ProfileMatch(
            level="explicit",
            label="不限专业可报",
            reason=publication.reason,
            priority=2,
        )

    major_status, major_reason = _major_status(job, profile)
    degree_status, degree_reason = _degree_status(job, profile)

    if publication.status == PUBLICATION_PENDING_EVIDENCE:
        return ProfileMatch(
            level="not_recommended",
            label="待补岗位级证据",
            reason=publication.reason,
            priority=0,
        )

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

    if major_status == "explicit":
        return ProfileMatch(
            level="review",
            label="需核验原公告",
            reason=f"{major_reason}；{degree_reason}",
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
    qualification_text = _qualification_text(job).lower()
    exact = [
        term
        for term in profile.exact_major_terms
        if _contains_exact_major_term(qualification_text, term)
    ]
    if exact:
        return "explicit", f"专业范围明确包含“{exact[0]}”"
    bounded = [
        term
        for term in profile.bounded_exact_major_terms
        if _contains_bounded_exact_major_term(qualification_text, term)
    ]
    if bounded:
        return "explicit", f"专业范围明确包含“{bounded[0]}”"
    explicit_english = [
        term
        for term in profile.english_exact_major_terms
        if _english_major_requirement_is_explicit(job, term)
    ]
    if explicit_english:
        return "explicit", f"公告学历条件明确包含“{explicit_english[0]}”"
    return "unspecified", "公告未明确列出目标专业"


def _qualification_text(job: dict[str, Any]) -> str:
    return qualification_evidence_text(job.get("field_evidence"))


def _degree_evidence_text(job: dict[str, Any]) -> str:
    field_evidence = job.get("field_evidence") or {}
    if not isinstance(field_evidence, dict):
        return ""
    values: list[str] = []
    for key, value in field_evidence.items():
        if str(key).casefold() in DEGREE_EVIDENCE_KEYS:
            values.append(str(value or ""))
        elif key == "fields" and isinstance(value, dict):
            values.extend(
                str(nested or "")
                for nested_key, nested in value.items()
                if str(nested_key).casefold() in DEGREE_EVIDENCE_KEYS
            )
    return clean_text(" ".join(values))


def _eligibility_evidence_text(job: dict[str, Any]) -> str:
    """Return row-bound eligibility conditions for the student-only veto.

    Professional matching stays intentionally stricter: it only reads named
    major fields.  Work-experience constraints, however, often live in a
    different cell of the same official attachment row.  Include those
    explicit condition columns and, for an ``official_attachment_row`` only,
    the preserved row text.  This prevents a mature-talent role from being
    published solely because its major and degree cells happen to match.
    """

    field_evidence = job.get("field_evidence") or {}
    if not isinstance(field_evidence, dict):
        return ""
    scope = str(field_evidence.get("evidence_scope") or "").strip()

    def collect(values: dict[str, Any], *, allow_row_text: bool) -> list[str]:
        items: list[str] = []
        for key, value in values.items():
            normalized_key = str(key).casefold()
            if key == "fields" and isinstance(value, dict):
                items.extend(collect(value, allow_row_text=allow_row_text))
                continue
            if normalized_key in ELIGIBILITY_EVIDENCE_KEYS:
                items.append(str(value or ""))
                continue
            if allow_row_text and normalized_key in ATTACHMENT_ROW_EVIDENCE_KEYS:
                items.append(str(value or ""))
        return items

    return clean_text(
        " ".join(
            value
            for value in collect(
                field_evidence,
                allow_row_text=scope == "official_attachment_row",
            )
            if value
        )
    )


def _has_job_level_evidence(job: dict[str, Any]) -> bool:
    """Require a verified row/detail locator before publishing a record.

    The row identity check blocks a subtle but serious failure mode: extracting
    a geology requirement from a multi-position notice and attaching it to a
    refinery, finance, or IT title on the same page.
    """
    field_evidence = job.get("field_evidence") or {}
    if not isinstance(field_evidence, dict):
        return False
    scope = str(field_evidence.get("evidence_scope") or "").strip()
    # Records extracted before this field existed can only retain their row
    # status when they have the explicit legacy table locator and row title.
    if not scope and str(field_evidence.get("table_row") or "").strip():
        scope = "official_html_table_row"
    if scope not in JOB_LEVEL_EVIDENCE_SCOPES:
        return False

    evidence_title = _evidence_title(field_evidence)
    job_title = clean_text(str(job.get("title") or ""))
    if not evidence_title or not job_title:
        return False
    normalized_evidence = evidence_title.casefold()
    normalized_job = job_title.casefold()
    return (
        normalized_evidence == normalized_job
        or normalized_evidence in normalized_job
        or normalized_job in normalized_evidence
    )


def _government_record_requires_location(job: dict[str, Any]) -> bool:
    source_id = str(job.get("source_id") or "").casefold()
    category = str(job.get("category") or "").casefold()
    markers = (
        "事业单位",
        "公务员",
        "自然资源",
        "地调与地勘",
        "geology-bureau",
        "hrss",
        "civil-service",
        "public-institution",
        "mnr-",
        "cgs-",
    )
    return any(marker.casefold() in source_id or marker.casefold() in category for marker in markers)


def _location_evidence_text(job: dict[str, Any]) -> str:
    values = [job.get("location"), job.get("province"), job.get("country_or_region")]
    field_evidence = job.get("field_evidence") or {}
    if isinstance(field_evidence, dict):
        for key, value in field_evidence.items():
            if str(key).casefold() in {"工作地点", "地点", "location", "work location"}:
                values.append(value)
    return clean_text(" ".join(str(value or "") for value in values))


def _evidence_title(field_evidence: dict[str, Any]) -> str:
    for key, value in field_evidence.items():
        if str(key).casefold() in JOB_TITLE_EVIDENCE_KEYS:
            return clean_text(str(value or ""))
        if key == "fields" and isinstance(value, dict):
            nested = _evidence_title(value)
            if nested:
                return nested
    return ""


def _profile_is_explicitly_eligible(
    job: dict[str, Any],
    profile: StudentProfile,
    qualification_text: str,
) -> bool:
    has_major = any(
        _contains_exact_major_term(qualification_text, term)
        for term in profile.exact_major_terms
    )
    has_bounded_major = any(
        _contains_bounded_exact_major_term(qualification_text, term)
        for term in profile.bounded_exact_major_terms
    )
    has_category = any(
        _contains_explicit_category_term(qualification_text, term)
        for term in profile.category_major_terms
    )
    # English role text frequently mentions a field in responsibilities or in
    # a neighbouring discipline name. Treat it as an eligibility condition
    # only when the same official requirement block gives it degree context.
    has_english_major = any(
        _english_major_requirement_is_explicit(job, term)
        for term in profile.english_exact_major_terms
    )
    return (
        has_major or has_bounded_major or has_category or has_english_major
    ) and _degree_status(job, profile)[0] == "explicit"


def _matched_profile_term(profile: StudentProfile, qualification_text: str) -> str:
    for term in profile.exact_major_terms:
        if _contains_exact_major_term(qualification_text, term):
            return term
    for term in profile.bounded_exact_major_terms:
        if _contains_bounded_exact_major_term(qualification_text, term):
            return term
    for term in profile.category_major_terms:
        if _contains_explicit_category_term(qualification_text, term):
            return term
    lowered = qualification_text.casefold()
    for term in profile.english_exact_major_terms:
        if _contains_english_exact_major_term(lowered, term):
            return term
    return profile.major


def _is_unrestricted_major(qualification_text: str) -> bool:
    lowered = qualification_text.casefold()
    return any(marker.casefold() in lowered for marker in UNRESTRICTED_MAJOR_MARKERS)


def _student_blocking_work_experience(qualification_text: str) -> str | None:
    """Return an experience condition unless the same record admits graduates."""
    if any(pattern.search(qualification_text) for pattern in FRESH_GRADUATE_EXEMPTION_PATTERNS):
        return None
    for pattern in EXPERIENCE_REQUIREMENT_PATTERNS:
        match = pattern.search(qualification_text)
        if match:
            return clean_text(match.group(0))
    return None


def _has_ambiguous_related_major_requirement(qualification_text: str) -> bool:
    lowered = qualification_text.casefold()
    markers = (
        "相关专业",
        "相近专业",
        "相关学科",
        "other related",
        "related discipline",
        "related geoscience",
        "related major",
    )
    return any(marker in lowered for marker in markers)


def _contains_exact_major_term(qualification_text: str, term: str) -> bool:
    """Avoid treating a first-level discipline as a nested narrower major."""
    lowered = qualification_text.casefold()
    candidate = term.casefold()
    if candidate not in lowered:
        return False
    # ``地质资源与地质工程`` is a first-level discipline. It must not turn
    # into a direct ``地质工程`` match merely because of a suffix overlap.
    if candidate == "地质工程" and "地质资源与地质工程" in lowered:
        remainder = lowered.replace("地质资源与地质工程", "")
        return candidate in remainder
    return True


def _contains_bounded_exact_major_term(qualification_text: str, term: str) -> bool:
    """Match a reviewed short-form Chinese discipline only as a full token.

    This deliberately rejects a longer neighbouring direction, for example
    ``应用地球物理`` or ``地球物理勘查`` for the ``地球物理`` short form.
    """
    candidate = term.casefold()
    if not candidate:
        return False
    lowered = qualification_text.casefold()
    boundary = r"[\s、，,；;。/()（）]"
    pattern = re.compile(
        rf"(?<![\u4e00-\u9fff]){re.escape(candidate)}"
        rf"(?=$|{boundary}|专业(?=$|{boundary})|类(?=$|{boundary}))"
    )
    return bool(pattern.search(lowered))


def _contains_explicit_category_term(qualification_text: str, term: str) -> bool:
    """Match a versioned discipline category in structured job evidence."""
    return term.casefold() in qualification_text.casefold()


def _contains_english_exact_major_term(text: str, term: str) -> bool:
    """Match a named English discipline, not a modified neighbouring field."""
    candidate = term.casefold()
    pattern = re.compile(rf"\b{re.escape(candidate)}\b", re.IGNORECASE)
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 40) : match.start()]
        # A qualification such as ``Petroleum Geology`` is a related field,
        # not an explicit statement that every ``Geology`` graduate qualifies.
        if re.search(
            r"\b(?:petroleum|exploration|applied|engineering|environmental|"
            r"marine|economic)\s+$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        return True
    return False


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
    if not has_major_qualification_evidence(job.get("field_evidence")):
        return False
    # Some ATS forms keep the degree and major in distinct fields. Both are
    # still one job-level evidence object, so they can be evaluated together
    # without falling back to announcement-wide prose.
    source_text = clean_text(
        f"{_qualification_text(job)} {_degree_evidence_text(job)}"
    ).lower()
    term_pattern = re.escape(major_term).replace(r"\ ", r"\s+")
    degree_context = (
        r"(?:bachelor(?:'s)?|master(?:'s)?|ph\.?d\.?|doctoral|"
        r"degree|major|discipline|field\s+of\s+study|qualification|"
        r"academic\s+background)"
    )
    if not _contains_english_exact_major_term(source_text, major_term):
        return False
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
    degree_text = _degree_evidence_text(job)
    listed = extract_degree_levels(degree_text)
    if profile.degree in listed:
        return "explicit", f"学历要求明确列出“{profile.degree}”"

    # A minimum junior-college floor is lower than every supported student
    # profile. Keep the raw wording in the job card while recognizing that
    # higher-degree students satisfy the minimum.
    if "大专" in listed and profile.degree in {"本科", "硕士", "博士"}:
        return "explicit", "公告最低学历为“大专/高职”，高学历学生满足学历门槛"

    source_text = degree_text.lower()
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
        # Chinese official tables commonly write the full degree name rather
        # than the abbreviated floor.  ``硕士研究生及以上`` and ``研究生及以上``
        # both include doctoral graduates; the latter must not be applied to
        # a phrase already qualified as ``博士研究生``.
        "硕士": ("硕士", "硕士研究生", "研究生", "master"),
        "博士": ("博士", "博士研究生", "ph.d", "phd", "doctoral"),
    }
    profile_rank = DEGREE_RANK[profile_degree]
    for degree, words in variants.items():
        if DEGREE_RANK[degree] > profile_rank:
            continue
        for word in words:
            if word == "研究生" and "博士研究生" in text:
                continue
            if word in text and any(
                phrase in text
                for phrase in (
                    f"{word}及以上",
                    f"{word}以上",
                    f"{word}学历及以上",
                    f"{word}学历以上",
                    f"{word}学位及以上",
                    f"{word}学位以上",
                    f"{word}或以上",
                    f"{word}学历或以上",
                    f"{word}学位或以上",
                    f"{word}以上学历",
                    f"{word}以上学位",
                    f"{word} or higher",
                    f"{word}'s or higher",
                    f"{word}'s degree or above",
                    f"{word} degree or above",
                )
            ):
                return True
    return False
