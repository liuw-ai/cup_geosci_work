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
    official_codes_for_profile,
    category_terms_for_profile,
    discipline_category_for_profile,
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
PUBLICATION_SIMILAR_ELIGIBLE = "similar_eligible"
PUBLICATION_PENDING_EVIDENCE = "pending_evidence"
PUBLICATION_OUT_OF_SCOPE = "out_of_scope"
PUBLICATION_SOURCE_UNAVAILABLE = "source_unavailable"
PUBLIC_STUDENT_PUBLICATION_STATUSES = frozenset(
    {
        PUBLICATION_STUDENT_ELIGIBLE,
        PUBLICATION_UNRESTRICTED_ELIGIBLE,
        PUBLICATION_SOURCE_UNAVAILABLE,
    }
)

# Explicit geoscience disciplines may be shown in a separate broad tier. They
# never masquerade as one of the four exact graduation majors, and a term alone
# is not enough: the official row must also have geoscience role context.
BROAD_GEOSCIENCE_TERMS = frozenset(
    {
        "地质类", "地质学类", "地质工程类", "地质资源与地质工程类", "地学类",
        "地球科学", "地质与矿产", "勘查技术与工程", "矿产普查与勘探",
        "矿产勘查", "资源勘查", "地球探测与信息技术", "地球信息科学与技术",
        "地球化学", "地球物理学", "地球物理", "固体地球物理学", "矿物学",
        "岩石学", "矿床学", "构造地质学", "古生物学与地层学", "第四纪地质学",
        "沉积学", "生态地质学", "海洋地质", "遥感地质", "石油地质", "油气地质",
        "工程地质", "水文地质", "环境地质", "地质灾害", "地热", "物探", "化探", "测井",
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

# ``publication_status`` answers whether a row may be shown to students.  This
# separate vocabulary answers how the official major evidence was classified
# for operations reporting. Explicit broad-geoscience rows are public only in a
# visibly separate tier; unresolved adjacent wording remains private.
MAJOR_MATCH_LEVELS = frozenset(
    {"exact", "similar", "broad", "pending", "not_recommended"}
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
PUBLIC_UNRESTRICTED_CATEGORIES = frozenset(
    {
        "事业单位与人才引进",
        "公务员与选调",
    }
)
PUBLIC_UNRESTRICTED_GOVERNMENT_TYPES = frozenset(
    {"public_institution", "civil_service"}
)
# Broad discipline wording is a valid public-sector recruitment rule, but it
# is not a substitute for a named graduation major on enterprise postings.
# Keeping this allow-list here makes the distinction explicit at the final
# publication gate instead of relying on a source name or industry label.
PUBLIC_BROAD_MAJOR_CATEGORIES = frozenset(
    {
        "事业单位与人才引进",
        "公务员与选调",
    }
)

# These are the only Chinese graduation-major names accepted for enterprise,
# university, postdoctoral and foreign-employer postings.  Public-sector
# position tables have a separate branch below for official discipline codes
# and broad categories; those rules must never widen this enterprise contract.
ENTERPRISE_EXACT_GRADUATION_MAJORS = frozenset(
    {
        "资源勘查工程",
        "地质学",
        "地质工程",
        "地质资源与地质工程",
    }
)

# Petroleum engineering is not one of the supported Geoscience School
# programs.  A narrowly defined, explicitly named petroleum-engineering-only
# requirement can therefore be closed without leaving a false "evidence
# missing" backlog merely because the notice ends with "等相关专业".  Do not
# use broad industry words such as "油气" or "储层" here: those may coexist
# with petroleum geology or geophysical directions that require review.
EXPLICIT_NON_TARGET_PETROLEUM_ENGINEERING_TERMS = frozenset(
    {
        "石油工程",
        "石油与天然气工程",
        "油气井工程",
        "油气田开发工程",
        "海洋油气工程",
        "油藏工程",
        "石油工程计算",
        "油气开采技术",
        "钻井工程",
        "钻井技术",
        "压裂工程",
    }
)
# These directions are deliberately not student profiles, but can be adjacent
# to the school's programs.  When a row combines one with petroleum-engineering
# text, its non-exhaustive "相关专业" wording remains private and pending.
AMBIGUOUS_GEOSCIENCE_REVIEW_TERMS = frozenset(
    {
        "地球物理",
        "岩石物理",
        "地震解释",
        "地震资料处理",
        "地球探测",
        "储层描述",
    }
)

# The public service is for current CUPB Geoscience students. A role can name
# one of their majors and still be unsuitable when it explicitly requires a
# multi-year employment record. This is deliberately narrow: it triggers only
# on an unambiguous work-experience condition in the same official job block.
EXPERIENCE_REQUIREMENT_PATTERNS = (
    re.compile(
        r"(?:具有|具备|需|要求|不少于|至少|满)?\s*"
        r"(?:[一二三四五六七八九十百千\d]+)\s*年(?:以上|以内)?"
        # Official role fields often put a long, specific domain phrase
        # between the year threshold and the final ``工作经历`` marker,
        # e.g. ``五年以上数据库建设应用或煤炭矿山、地质类相关工作经历``.
        # Keep the match inside one requirement sentence while allowing that
        # bounded detail to be captured.
        r"[^。；;\n]{0,96}?(?:工作|从业|项目|行业|工程|勘察|设计)(?:经验|经历)"
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
    official_major_codes: tuple[str, ...] = ()
    bounded_exact_major_terms: tuple[str, ...] = ()
    category_major_terms: tuple[str, ...] = ()
    english_exact_major_terms: tuple[str, ...] = ()
    related_major_terms: tuple[str, ...] = ()
    discipline_category: str = ""
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
    public_scope: str = "domestic"
    # Stable audit label. Enterprise rows may only use the four named majors
    # (or their explicit English equivalents); public recruitment has a
    # separate broad-category/code rule.
    major_match_mode: str = ""

    @property
    def is_public(self) -> bool:
        return self.status in PUBLIC_STUDENT_PUBLICATION_STATUSES

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_profile_ids"] = list(self.matched_profile_ids)
        return payload


# These seven profiles mirror the programs specified for CUPB Geoscience School.
# The aliases only make an announcement easier to recognize; they never create a
# claim that a student is eligible when the original notice says otherwise.
def _profile(profile_id: str, degree: str, major: str) -> StudentProfile:
    """Build a profile from the versioned taxonomy instead of local aliases."""
    return StudentProfile(
        id=profile_id,
        degree=degree,
        major=major,
        exact_major_terms=exact_terms_for_profile(profile_id),
        official_major_codes=official_codes_for_profile(profile_id),
        bounded_exact_major_terms=bounded_exact_terms_for_profile(profile_id),
        category_major_terms=category_terms_for_profile(profile_id),
        english_exact_major_terms=english_exact_terms_for_profile(profile_id),
        related_major_terms=related_terms_for_profile(profile_id),
        discipline_category=discipline_category_for_profile(profile_id),
        taxonomy_id=profile_major_definition(profile_id).id,
    )


STUDENT_PROFILES = (
    _profile("undergraduate-resource-exploration", "本科", "资源勘查工程"),
    _profile("master-geology", "硕士", "地质学"),
    _profile("master-geological-engineering", "硕士", "地质工程"),
    _profile("master-geological-resources-engineering", "硕士", "地质资源与地质工程"),
    _profile("doctoral-geology", "博士", "地质学"),
    _profile("doctoral-geological-engineering", "博士", "地质工程"),
    _profile("doctoral-geological-resources-engineering", "博士", "地质资源与地质工程"),
)


def list_student_profiles() -> tuple[StudentProfile, ...]:
    return STUDENT_PROFILES


def get_student_profile(profile_id: str | None) -> StudentProfile | None:
    if not profile_id:
        return None
    return next((item for item in STUDENT_PROFILES if item.id == profile_id), None)


def evaluate_student_publication(
    job: dict[str, Any],
    *,
    allow_non_domestic: bool = False,
) -> PublicationDecision:
    """Decide whether an official job belongs in the public student corpus.

    Employer industry, announcement prose and historical keyword tags cannot
    publish a job. The decision accepts an explicit target major, a separately
    labelled adjacent-geoscience major, or an explicit public-sector
    ``unrestricted major`` statement, together with an official degree field
    that covers at least one supported student profile.
    """
    scope_exclusion = _student_program_scope_exclusion(job)
    if scope_exclusion is not None:
        label, reason = scope_exclusion
        return PublicationDecision(
            status=PUBLICATION_OUT_OF_SCOPE,
            label=label,
            reason=reason,
            matched_profile_ids=(),
        )

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

    evidence = job.get("field_evidence")
    if isinstance(evidence, dict) and evidence.get("地点拆分基准岗位"):
        variant = clean_text(str(evidence.get("官方工作地点") or ""))
        displayed = clean_text(str(job.get("location") or ""))
        if variant and displayed and variant != displayed:
            return PublicationDecision(
                status=PUBLICATION_PENDING_EVIDENCE,
                label="待核拆分地点",
                reason="岗位已按官方地点拆分，但展示地点与该地点变体不一致。",
                matched_profile_ids=(),
            )

    location_scope = _domestic_location_scope_decision(
        job,
        allow_non_domestic=allow_non_domestic,
    )
    if location_scope is not None:
        return location_scope

    public_scope = (
        "international"
        if str(job.get("country_or_region") or "").strip()
        not in MAINLAND_COUNTRY_LABELS
        else "domestic"
    )

    required_experience = student_blocking_work_experience_requirement(job)
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
            public_scope=public_scope,
            major_match_mode=(
                "public_named_or_code"
                if _public_broad_major_scope(job)
                else "exact_four_names"
            ),
        )

    if _is_unrestricted_major(qualification_text):
        if not _unrestricted_major_is_allowed_for_public_role(job):
            return PublicationDecision(
                status=PUBLICATION_OUT_OF_SCOPE,
                label="非公共岗位不限专业",
                reason=(
                    "官方岗位虽标注不限专业，但岗位类别不是事业单位、公务员、"
                    "等公共招录；企业、高校和博士后岗位仍需明确目标毕业专业。"
                ),
                matched_profile_ids=(),
            )
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
                public_scope=public_scope,
                major_match_mode="public_unrestricted_rule",
            )

    if _is_explicit_non_target_petroleum_engineering_requirement(qualification_text):
        return PublicationDecision(
            status=PUBLICATION_OUT_OF_SCOPE,
            label="石油工程方向不匹配",
            reason=(
                "官方岗位级专业要求仅列石油工程及油气开发方向，"
                "不覆盖本院确认的培养专业。"
            ),
            matched_profile_ids=(),
        )

    broad_terms = _broad_geoscience_terms(qualification_text)
    if broad_terms and _has_geoscience_role_evidence(job):
        degree_profiles = tuple(
            profile.id
            for profile in STUDENT_PROFILES
            if _degree_status(job, profile)[0] == "explicit"
        )
        if degree_profiles:
            return PublicationDecision(
                status=PUBLICATION_STUDENT_ELIGIBLE,
                label="专业范围宽泛",
                reason=(
                    "官方岗位级专业要求包含明确地学方向（"
                    + "、".join(broad_terms)
                    + "），但未直接限定为四个核心毕业专业；学生需以原公告资格审查为准。"
                ),
                matched_profile_ids=degree_profiles,
                public_scope=public_scope,
                major_match_mode="broad_geoscience",
            )

    related = _related_major_matches(job, qualification_text)
    if related:
        profile_ids = tuple(item["profile_id"] for item in related)
        terms = tuple(dict.fromkeys(item["term"] for item in related))
        if not _has_geoscience_role_evidence(job):
            return PublicationDecision(
                status=PUBLICATION_PENDING_EVIDENCE,
                label="待核岗位方向",
                reason=(
                    "官方专业要求包含相近地学方向（"
                    + "、".join(terms)
                    + "），但岗位名称或职责未证明工作属于地学方向。"
                ),
                matched_profile_ids=profile_ids,
            )
        return PublicationDecision(
            status=PUBLICATION_SIMILAR_ELIGIBLE,
            label="相近专业机会",
            reason=(
                "官方岗位级专业要求明确出现相近地学方向（"
                + "、".join(terms)
                + "），但未出现四个毕业专业原名；学生需以原公告最终资格审查为准。"
            ),
            matched_profile_ids=profile_ids,
            public_scope=public_scope,
            major_match_mode="similar_major",
        )

    if _has_ambiguous_related_major_requirement(qualification_text):
        if not _has_geoscience_requirement_marker(qualification_text):
            return PublicationDecision(
                status=PUBLICATION_OUT_OF_SCOPE,
                label="专业不匹配",
                reason="官方岗位级专业要求未覆盖本院目标专业或明确相近地学方向。",
                matched_profile_ids=(),
            )
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


def classify_major_match(job: dict[str, Any]) -> dict[str, Any]:
    """Classify official major evidence without changing the publish gate.

    ``evaluate_student_publication`` remains the sole publication authority.
    This projection is intentionally additive: it lets the source funnel and
    coverage report distinguish exact names, public-sector broad rules,
    adjacent geoscience wording, and unresolved evidence. A ``similar`` row
    remains an internal review classification only; it is not part of the
    formal student-facing vacancy corpus and is never relabelled as an exact
    graduation-major match.
    """

    decision = evaluate_student_publication(job)
    qualification = _qualification_text(job)
    exact_profile_ids = tuple(
        profile.id
        for profile in STUDENT_PROFILES
        if _profile_has_named_exact_match(job, profile, qualification)
    )
    if exact_profile_ids:
        return {
            "level": "exact",
            "label": "\u7cbe\u786e\u5339\u914d",
            "reason": "\u5c97\u4f4d\u7ea7\u4e13\u4e1a\u8981\u6c42\u660e\u786e\u51fa\u73b0\u56db\u4e2a\u6bd5\u4e1a\u4e13\u4e1a\u4e4b\u4e00\u3002",
            "matched_profile_ids": list(exact_profile_ids),
            "publication_status": decision.status,
            "major_match_mode": decision.major_match_mode,
        }

    # Public position tables may use official catalogue codes, discipline
    # categories, or an explicit unrestricted-major rule.  Those are useful
    # to students but are not equivalent to a named enterprise major.
    if decision.major_match_mode in {
        "public_named_or_code",
        "public_unrestricted_rule",
        "broad_geoscience",
    } or decision.status == PUBLICATION_UNRESTRICTED_ELIGIBLE:
        label = (
            "专业范围宽泛"
            if decision.major_match_mode == "broad_geoscience"
            else "公共招录宽泛规则"
        )
        return {
            "level": "broad",
            "label": label,
            "reason": decision.reason,
            "matched_profile_ids": list(decision.matched_profile_ids),
            "publication_status": decision.status,
            "major_match_mode": decision.major_match_mode,
        }

    related = _related_major_matches(job, qualification)
    if related and decision.status == PUBLICATION_SIMILAR_ELIGIBLE:
        profile_ids = tuple(item["profile_id"] for item in related)
        terms = tuple(item["term"] for item in related)
        return {
            "level": "similar",
            "label": "\u76f8\u8fd1\u4e13\u4e1a",
            "reason": (
                "\u5c97\u4f4d\u7ea7\u4e13\u4e1a\u8981\u6c42\u51fa\u73b0\u76f8\u8fd1\u5730\u5b66\u65b9\u5411\uff08"
                + "、".join(terms)
                + "\uff09\uff0c\u4f46\u672a\u51fa\u73b0\u56db\u4e2a\u6bd5\u4e1a\u4e13\u4e1a\u539f\u540d\uff1b\u4e0d\u80fd\u66ff\u4ee3\u7cbe\u786e\u5339\u914d\u3002"
            ),
            "matched_profile_ids": list(profile_ids),
            "matched_terms": list(terms),
            "publication_status": decision.status,
            "major_match_mode": decision.major_match_mode,
        }

    level = "pending" if decision.status == PUBLICATION_PENDING_EVIDENCE else "not_recommended"
    return {
        "level": level,
        "label": "\u5f85\u6838\u9a8c" if level == "pending" else "\u4e0d\u63a8\u8350",
        "reason": decision.reason,
        "matched_profile_ids": list(decision.matched_profile_ids),
        "publication_status": decision.status,
        "major_match_mode": decision.major_match_mode,
    }


def _broad_geoscience_terms(qualification_text: str) -> tuple[str, ...]:
    """Return explicit broad-geoscience terms in official major evidence."""

    normalized = clean_text(qualification_text)
    return tuple(
        term
        for term in sorted(BROAD_GEOSCIENCE_TERMS, key=lambda item: (-len(item), item))
        if _contains_related_major_term(normalized, term.casefold())
    )


def _profile_has_named_exact_match(
    job: dict[str, Any],
    profile: StudentProfile,
    qualification_text: str,
) -> bool:
    if any(
        _contains_exact_major_term(qualification_text, term)
        for term in profile.exact_major_terms
    ):
        return True
    if any(
        _contains_bounded_exact_major_term(qualification_text, term)
        for term in profile.bounded_exact_major_terms
    ):
        return True
    return any(
        _english_major_requirement_is_explicit(job, term)
        for term in profile.english_exact_major_terms
    )


def _related_major_matches(
    job: dict[str, Any],
    qualification_text: str,
) -> list[dict[str, str]]:
    if not qualification_text or not _has_job_level_evidence(job):
        return []
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for profile in STUDENT_PROFILES:
        # A similar-major label still requires an explicit degree floor for
        # the profile. This prevents a doctoral-only adjacent direction from
        # being presented to an undergraduate merely because the keyword is
        # present in the same official row.
        if _degree_status(job, profile)[0] != "explicit":
            continue
        for term in profile.related_major_terms:
            normalized = clean_text(term).casefold()
            if not normalized or not _contains_related_major_term(
                qualification_text, normalized
            ):
                continue
            key = (profile.id, normalized)
            if key in seen:
                continue
            seen.add(key)
            matches.append({"profile_id": profile.id, "term": clean_text(term)})
    return matches


def _contains_related_major_term(qualification_text: str, term: str) -> bool:
    """Match an adjacent major without accepting it inside a longer name."""

    lowered = clean_text(qualification_text).casefold()
    candidate = clean_text(term).casefold()
    if not candidate:
        return False
    for match in re.finditer(re.escape(candidate), lowered):
        start, end = match.span()
        if (
            start > 0
            and "\u4e00" <= lowered[start - 1] <= "\u9fff"
            and lowered[start - 1] not in {"或", "及", "和", "与", "、"}
        ):
            continue
        if end < len(lowered) and "\u4e00" <= lowered[end] <= "\u9fff":
            if not lowered[end:].startswith(
                ("专业", "及", "或", "和", "与", "等", "相关", "方向", "类", "、")
            ):
                continue
        return True
    return False


def _has_geoscience_requirement_marker(qualification_text: str) -> bool:
    """Distinguish a real adjacent-geoscience requirement from generic prose."""

    lowered = clean_text(qualification_text).casefold()
    markers = (
        "地质",
        "地球",
        "地学",
        "矿产",
        "勘查",
        "勘探",
        "物探",
        "测井",
        "地震",
        "储层",
        "地球化学",
        "遥感",
        "沉积",
        "海洋地质",
        "石油地质",
        "油气地质",
        "资源勘查",
        "工程地质",
        "水文地质",
        "环境地质",
        "geolog",
        "geophys",
        "geoscience",
        "seismic",
        "subsurface",
        "well logging",
    )
    return any(marker.casefold() in lowered for marker in markers)


_GEOSCIENCE_ROLE_MARKERS = (
    "地质", "地勘", "地学", "地球物理", "地球化学", "矿产", "矿床", "岩矿鉴定", "找矿", "勘查", "勘探", "物探", "化探",
    "测井", "岩石物理", "矿物", "岩石", "地震", "储层", "岩土", "地灾", "地热", "沉积", "水工环",
    "自然资源", "生态地质",
    "geolog", "geophys", "geoscience", "seismic", "subsurface", "well logging",
)
_NON_GEOSCIENCE_ROLE_TITLE = re.compile(
    r"出版|编辑|教师|教学|职能类|ESG|管培生|矿物加工|选矿|销售|市场营销|"
    r"投资管理|财务|人力资源|行政|法务|宣传|采购",
    re.I,
)
_DUTY_START = re.compile(
    r"(?:岗位|工作|职位)(?:职责|描述|内容|任务)|主要职责|研究方向|"
    r"职责描述|responsibilities|job duties",
    re.I,
)
_DUTY_END = re.compile(
    r"(?:任职|应聘|职位|岗位)(?:条件|要求|资格)|专业要求|学历要求|"
    r"薪资|福利|qualifications|requirements",
    re.I,
)


def _related_role_context(job: dict[str, Any]) -> str:
    values = [job.get("title")]
    field_evidence = job.get("field_evidence")
    if isinstance(field_evidence, dict):
        role_keys = {
            "\u5c97\u4f4d", "\u5c97\u4f4d\u540d\u79f0", "\u62db\u8058\u5c97\u4f4d", "\u804c\u4f4d", "\u804c\u4f4d\u540d\u79f0", "\u5c97\u4f4d\u804c\u8d23",
            "\u804c\u8d23", "\u5de5\u4f5c\u5185\u5bb9", "\u7814\u7a76\u65b9\u5411", "\u5c97\u4f4d\u65b9\u5411", "role", "job title",
            "job duties", "responsibilities",
        }
        normalized_role_keys = {item.casefold() for item in role_keys}
        for key, value in field_evidence.items():
            if str(key).casefold() in normalized_role_keys:
                values.append(value)
            elif key == "fields" and isinstance(value, dict):
                values.extend(
                    nested
                    for nested_key, nested in value.items()
                    if str(nested_key).casefold() in normalized_role_keys
                )
    description = clean_text(str(job.get("description") or ""))
    duty_start = _DUTY_START.search(description)
    if duty_start:
        duties = description[duty_start.end():]
        duty_end = _DUTY_END.search(duties)
        values.append(duties[:duty_end.start() if duty_end else 4000])
    return clean_text(" ".join(value for value in values if isinstance(value, str)))


def _has_geoscience_role_evidence(job: dict[str, Any]) -> bool:
    title = clean_text(str(job.get("title") or "")).casefold()
    if _NON_GEOSCIENCE_ROLE_TITLE.search(title):
        return False
    role_text = _related_role_context(job).casefold()
    return any(marker in role_text for marker in _GEOSCIENCE_ROLE_MARKERS)


def student_blocking_work_experience_requirement(
    job: dict[str, Any],
) -> str | None:
    """Return a job-level experience condition incompatible with fresh graduates.

    This is intentionally a diagnostic helper as well as the publication
    gate's implementation.  An incomplete location or degree field must not
    hide a separately captured multi-year experience requirement from
    evidence audits.
    """

    return _student_blocking_work_experience(_eligibility_evidence_text(job))


_GENERIC_NON_DOMESTIC_LABELS = frozenset({"海外", "境外", "国外", "境内外混合"})


_ROLE_SCOPE_FIELD_KEYS = frozenset(
    {
        "岗位类型",
        "岗位性质",
        "招聘类型",
        "招聘性质",
        "用工类型",
        "职位类型",
        "职位性质",
        "招录类型",
        "项目类型",
        "job type",
        "job category",
        "employment type",
        "position type",
        "program type",
    }
)


def _student_program_scope_exclusion(
    job: dict[str, Any],
) -> tuple[str, str] | None:
    """Reject role types outside the confirmed student-facing service scope.

    The site serves graduates, campus recruits, public-sector recruitment,
    postdoctoral and university/research vacancies.  A detail can still
    mention ``实习经历`` as a preferred qualification, so the internship
    veto is limited to role titles, explicit type fields, and unambiguous
    recruitment phrases rather than every occurrence of the word.
    """

    title = clean_text(str(job.get("title") or ""))
    title_lower = title.casefold()
    internship_title = re.search(
        r"(?:实习生?|见习生?|intern(?:ship)?|trainee)",
        title_lower,
        re.IGNORECASE,
    )
    if internship_title:
        return (
            "实习/见习岗位",
            "岗位名称明确为实习、见习或 intern/trainee，不属于本阶段学生就业岗位范围。",
        )

    # Broad official job-board searches can return procurement workflow roles
    # whose major field happens to include a target discipline (for example,
    # "招标业务员" with a broad resource-exploration major list).  These are
    # not geoscience employment duties.  Restrict this veto to the title so a
    # legitimate technical role that merely mentions procurement in its
    # responsibilities remains eligible.
    if re.search(r"(?:采购|招标|投标|中标)", title):
        return (
            "非地学业务岗位",
            "岗位名称属于采购、招标或投标流程职能，不属于本院地学就业岗位。",
        )

    field_evidence = job.get("field_evidence") or {}
    role_type_text: list[str] = []
    role_type_keys = {item.casefold() for item in _ROLE_SCOPE_FIELD_KEYS}

    def collect_role_types(values: dict[str, Any]) -> None:
        for key, value in values.items():
            if key == "fields" and isinstance(value, dict):
                collect_role_types(value)
                continue
            normalized_key = str(key).strip().casefold()
            if normalized_key in role_type_keys:
                role_type_text.append(clean_text(str(value or "")))

    if isinstance(field_evidence, dict):
        collect_role_types(field_evidence)
    role_type = clean_text(" ".join(role_type_text)).casefold()
    description = clean_text(
        " ".join(
            str(job.get(key) or "")
            for key in ("summary", "description", "qualification_text")
        )
    ).casefold()
    if role_type and re.search(
        r"(?:实习生?|见习生?|intern(?:ship)?|trainee)", role_type, re.IGNORECASE
    ):
        return (
            "实习/见习岗位",
            "官方岗位类型明确为实习、见习或 intern/trainee，不属于本阶段学生就业岗位范围。",
        )

    # Some official portals keep the recruitment channel in a structured
    # ``职位性质``/``招聘类型`` field and omit it from the free-form
    # description.  Treat the explicit social-hire marker as a scope veto,
    # while preserving the agreed postdoctoral exception.
    if role_type and re.search(
        r"(?:社会招聘|社会招录|社会招|社招|成熟人才招聘|"
        r"experienced\s+(?:professional|hire|hiring)|experienced\s+professionals?)",
        role_type,
        re.IGNORECASE,
    ) and not _is_explicit_postdoctoral_vacancy(
        title=title,
        field_evidence=field_evidence,
        description=description,
    ):
        return (
            "社会招聘岗位",
            "官方岗位类型明确为社会招聘或成熟人才招聘，本阶段仅收录应届/校园及公共就业等岗位。",
        )

    # These phrases identify the vacancy itself, while deliberately excluding
    # normal requirements such as "有实习经历者优先".
    internship_role_phrase = re.search(
        r"(?:实习岗位|实习招聘|实习项目|见习岗位|见习招聘|见习项目|"
        r"internship\s+(?:position|role|program|opportunity)|"
        r"(?:intern|trainee)\s+(?:position|role|program|opportunity))",
        description,
        re.IGNORECASE,
    )
    if internship_role_phrase:
        return (
            "实习/见习岗位",
            "官方岗位说明明确为实习或见习招聘，不属于本阶段学生就业岗位范围。",
        )

    social_phrase = re.search(
        r"(?:社会招聘|社会招录|社会招|社招|成熟人才招聘|"
        r"experienced\s+(?:professional|hire|hiring)|experienced\s+professionals?)",
        description,
        re.IGNORECASE,
    )
    if social_phrase and not _is_explicit_postdoctoral_vacancy(
        title=title,
        field_evidence=field_evidence,
        description=description,
    ):
        return (
            "社会招聘岗位",
            "官方岗位说明明确为社会招聘或成熟人才招聘，本阶段仅收录应届/校园及公共就业等岗位。",
        )

    return None


def _is_explicit_postdoctoral_vacancy(
    *,
    title: str,
    field_evidence: object,
    description: str,
) -> bool:
    """Recognize the agreed postdoctoral exception without widening social hires.

    A recruitment board can classify a genuine postdoctoral vacancy as a
    social posting. The service explicitly includes postdoctoral positions,
    but a role merely based at a ``博士后工作站`` is not sufficient evidence
    that it is a postdoctoral appointment.
    """

    normalized_title = clean_text(title)
    if "博士后" in normalized_title and "博士后工作站" not in normalized_title:
        return True

    if isinstance(field_evidence, dict):
        role_keys = {item.casefold() for item in _ROLE_SCOPE_FIELD_KEYS}
        for key, value in field_evidence.items():
            if str(key).strip().casefold() not in role_keys:
                continue
            role_type = clean_text(str(value or ""))
            if "博士后" in role_type and "博士后工作站" not in role_type:
                return True

    return bool(
        re.search(
            r"(?:博士后(?:岗位|招聘|招收|研究人员)|postdoctoral\s+"
            r"(?:position|role|fellow|researcher|recruitment))",
            description,
            re.IGNORECASE,
        )
    )


def _domestic_location_scope_decision(
    job: dict[str, Any],
    *,
    allow_non_domestic: bool = False,
) -> PublicationDecision | None:
    """Apply the location scope gate before professional matching.

    A source may explicitly opt in to international jobs, but generic labels
    such as ``海外`` and ``境内外混合`` remain private because they do not
    provide an unambiguous work location for students.
    """

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
    # This application serves China Petroleum University (Beijing) students
    # and its production target is current mainland-China vacancies.  A source
    # may still discover an overseas role for audit, but no source-level opt-in
    # can turn it into a student-facing opportunity.  Keeping this gate here,
    # before major/degree matching, prevents international rows from being
    # marked eligible and later leaking through an alternate read path.
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
    publication_basis = job.get("publication_basis")
    allow_non_domestic = (
        isinstance(publication_basis, dict)
        and publication_basis.get("public_scope") == "international"
    )
    publication = evaluate_student_publication(
        job,
        allow_non_domestic=allow_non_domestic,
    )
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
    if _public_broad_major_scope(job):
        code = next(
            (
                code
                for code in profile.official_major_codes
                if _contains_official_major_code(qualification_text, code)
            ),
            None,
        )
        if code:
            return "explicit", f"公共招录职位表专业代码明确包含“{code}”"
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
    if _public_broad_major_scope(job):
        category = next(
            (
                term
                for term in profile.category_major_terms
                if _contains_explicit_category_term(qualification_text, term)
            ),
            None,
        )
        if category:
            return "explicit", f"公共招录职位表专业类别明确包含“{category}”"
        if _contains_explicit_discipline_category(
            qualification_text, profile.discipline_category
        ):
            return "explicit", f"公共招录职位表明确要求“{profile.discipline_category}大类”"
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
    # Some official portals put the education floor at the start of the
    # structured major field (for example, ``本科及以上学历，地质工程...``)
    # while their separate education field is only ``本科``. Preserve only
    # an explicit floor phrase; never treat arbitrary major prose as degree
    # evidence.
    qualification = qualification_evidence_text(field_evidence)
    floor_matches = re.findall(
        r"(?:大专|专科|高职|本科|学士|硕士|研究生|博士|博士研究生|"
        r"bachelor(?:'s)?|master(?:'s)?|undergraduate|doctoral)"
        r"[^。；;\n]{0,12}?"
        r"(?:及以上|以上|或以上|or\s+higher|or\s+above)",
        qualification,
        flags=re.IGNORECASE,
    )
    values.extend(floor_matches)
    return clean_text(" ".join(dict.fromkeys(values)))


def _eligibility_evidence_text(job: dict[str, Any]) -> str:
    """Return row-bound eligibility conditions for the student-only veto.

    Professional matching stays intentionally stricter: it only reads named
    major fields.  Work-experience constraints, however, often live in a
    different cell of the same official attachment row or are appended to a
    portal's structured ``专业范围`` value.  Include those explicit condition
    columns and the bounded major-requirement fields, plus (for an
    ``official_attachment_row`` only) the preserved row text.  This prevents
    a mature-talent role from being published solely because its major and
    degree cells happen to match.
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

    values = collect(
        field_evidence,
        allow_row_text=scope == "official_attachment_row",
    )
    # A few dynamic portals place the complete requirement sentence in the
    # major field, including a years-of-experience clause.  The matching
    # helper already restricts this extraction to the allow-listed major
    # evidence keys, so reusing it here does not pull arbitrary page prose
    # into the student-only veto.
    qualification = qualification_evidence_text(field_evidence)
    if qualification:
        values.append(qualification)
    return clean_text(" ".join(value for value in values if value))


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
    public_broad_scope = _public_broad_major_scope(job)
    has_official_code = public_broad_scope and any(
        _contains_official_major_code(qualification_text, code)
        for code in profile.official_major_codes
    )
    has_bounded_major = public_broad_scope and any(
        _contains_bounded_exact_major_term(qualification_text, term)
        for term in profile.bounded_exact_major_terms
    )
    has_category = public_broad_scope and any(
        _contains_explicit_category_term(qualification_text, term)
        for term in profile.category_major_terms
    )
    has_discipline_category = public_broad_scope and _contains_explicit_discipline_category(
        qualification_text, profile.discipline_category
    )
    # English role text frequently mentions a field in responsibilities or in
    # a neighbouring discipline name. Treat it as an eligibility condition
    # only when the same official requirement block gives it degree context.
    has_english_major = any(
        _english_major_requirement_is_explicit(job, term)
        for term in profile.english_exact_major_terms
    )
    return (
        has_major
        or has_official_code
        or has_bounded_major
        or has_category
        or has_discipline_category
        or has_english_major
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
    if _contains_explicit_discipline_category(
        qualification_text, profile.discipline_category
    ):
        return f"{profile.discipline_category}大类"
    lowered = qualification_text.casefold()
    for term in profile.english_exact_major_terms:
        if _contains_english_exact_major_term(lowered, term):
            return term
    return profile.major


def _is_unrestricted_major(qualification_text: str) -> bool:
    lowered = qualification_text.casefold()
    return any(marker.casefold() in lowered for marker in UNRESTRICTED_MAJOR_MARKERS)


def _unrestricted_major_is_allowed_for_public_role(job: dict[str, Any]) -> bool:
    """Limit unrestricted-major publication to agreed public-sector scopes."""

    category = clean_text(str(job.get("category") or ""))
    if category in PUBLIC_UNRESTRICTED_CATEGORIES:
        return True
    evidence = job.get("field_evidence")
    if isinstance(evidence, dict):
        government_type = clean_text(str(evidence.get("政府岗位类型") or ""))
        if government_type in PUBLIC_UNRESTRICTED_GOVERNMENT_TYPES:
            return True
    # University, research and postdoctoral vacancies are not civil-service
    # or public-institution position-table rules. They must name one of the
    # four graduation majors explicitly; an ``不限专业`` phrase here is not a
    # substitute for that requirement.
    return False


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


def _is_explicit_non_target_petroleum_engineering_requirement(
    qualification_text: str,
) -> bool:
    """Identify only the agreed, unambiguous petroleum-engineering exclusion.

    This function executes after target-major and public-sector-unrestricted
    checks.  It must preserve rows that name a target major but fail a degree
    check, as well as geophysical/rock-physics wording whose "related major"
    scope cannot be proved from the official row alone.
    """

    if not _has_ambiguous_related_major_requirement(qualification_text):
        return False
    lowered = qualification_text.casefold()
    if not any(term.casefold() in lowered for term in EXPLICIT_NON_TARGET_PETROLEUM_ENGINEERING_TERMS):
        return False
    if any(term.casefold() in lowered for term in AMBIGUOUS_GEOSCIENCE_REVIEW_TERMS):
        return False
    for profile in STUDENT_PROFILES:
        profile_terms = (
            *profile.exact_major_terms,
            *profile.bounded_exact_major_terms,
            *profile.category_major_terms,
            *profile.related_major_terms,
            profile.major,
        )
        if any(term.casefold() in lowered for term in profile_terms if term):
            return False
    return True


def _contains_exact_major_term(qualification_text: str, term: str) -> bool:
    """Match a named graduation major without substring false positives.

    A requirement field such as ``地质工程技术`` is not the graduation major
    ``地质工程``.  Conversely, ``地质工程及相关专业`` is a valid explicit
    mention because the target name is a complete token in the official major
    field.  Keep the accepted suffixes narrow and leave broad categories to
    the public-recruitment branch.
    """
    lowered = qualification_text.casefold()
    candidate = term.casefold()
    if candidate not in lowered:
        return False
    if candidate not in {
        value.casefold() for value in ENTERPRISE_EXACT_GRADUATION_MAJORS
    }:
        return False

    # Validate every occurrence.  A requirement may list an invalid longer
    # term first and the exact graduation major later (for example
    # ``地质工程技术、地质工程``).  Returning on the first occurrence would
    # incorrectly discard the valid latter token.
    for match in re.finditer(re.escape(candidate), lowered):
        start = match.start()
        end = match.end()

        # ``地质资源与地质工程`` is a first-level discipline.  Its suffix
        # must not satisfy the shorter ``地质工程`` enterprise contract.
        if candidate == "地质工程" and lowered[:end].endswith("地质资源与地质工程"):
            continue

        # ``地质学类``/``地质工程类`` is a discipline category, not the
        # literal graduation-major name.  Category wording is admitted only
        # through the public recruitment branch below.
        if lowered[end:].startswith(("类", "大类")):
            continue

        # All supported enterprise terms are Chinese names. Reject a match
        # when an adjacent Chinese character forms a longer discipline or job
        # title; permit only connectors routinely used in official
        # requirement fields (专业、及、或、等、相关 and punctuation).
        if (
            start > 0
            and "\u4e00" <= lowered[start - 1] <= "\u9fff"
            and lowered[start - 1] not in {"或", "及", "和", "与"}
        ):
            continue
        if end < len(lowered) and "\u4e00" <= lowered[end] <= "\u9fff":
            suffix = lowered[end:]
            if not suffix.startswith(
                (
                    "专业",
                    "及",
                    "或",
                    "等",
                    "相关",
                    "毕业",
                    "学历",
                    "本科",
                    "硕士",
                    "博士",
                    "研究生",
                )
            ):
                continue
        return True
    return False


def _contains_official_major_code(qualification_text: str, code: str) -> bool:
    """Match a catalogue code only as a complete numeric token."""
    candidate = clean_text(code)
    if not candidate:
        return False
    return bool(
        re.search(
            rf"(?<![0-9]){re.escape(candidate)}(?![0-9])",
            qualification_text.casefold(),
        )
    )


def _public_broad_major_scope(job: dict[str, Any]) -> bool:
    """Whether this row is a public recruitment rule, not an enterprise job."""
    category = clean_text(str(job.get("category") or ""))
    if category in PUBLIC_BROAD_MAJOR_CATEGORIES:
        return True
    evidence = job.get("field_evidence")
    if isinstance(evidence, dict):
        government_type = clean_text(str(evidence.get("政府岗位类型") or ""))
        if government_type in PUBLIC_UNRESTRICTED_GOVERNMENT_TYPES:
            return True
    return False


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


def _contains_explicit_discipline_category(
    qualification_text: str, discipline_category: str
) -> bool:
    """Match only official broad-discipline wording.

    Bare mentions in job duties are intentionally ignored.  The caller has
    already restricted this text to the structured professional requirement.
    """
    category = clean_text(discipline_category)
    if not category:
        return False
    lowered = qualification_text.casefold()
    suffixes = ("大类", "类", "门类", "学科门类")
    for suffix in suffixes:
        token = f"{category}{suffix}".casefold()
        start = 0
        while True:
            index = lowered.find(token, start)
            if index < 0:
                break
            # Do not match the ``理学类`` suffix inside ``管理学类`` (or
            # another longer Chinese discipline). Allow common structural
            # prefixes such as ``包括理学类`` and ``属于理学类``.
            prefix = lowered[max(0, index - 4) : index]
            previous = lowered[index - 1] if index else ""
            if not (previous and "\u4e00" <= previous <= "\u9fff") or prefix.endswith(
                ("包括", "属于", "要求", "专业", "招收")
            ):
                return True
            start = index + 1
    return False


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
