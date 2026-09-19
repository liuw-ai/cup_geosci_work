from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from job_hub.profiles import evaluate_profile_match, list_student_profiles


@dataclass(frozen=True)
class SyntheticStudent:
    """A deterministic, anonymous record used only for offline quality checks."""

    id: str
    profile_id: str


# 40 undergraduate, 42 master's, and 18 doctoral students. The distribution is
# intentionally fixed so every run is comparable and all seven requested tracks
# are exercised. These records are never written to the public site's database.
COHORT_PLAN = (
    ("undergraduate-resource-exploration", 40),
    ("master-geology", 14),
    ("master-geological-engineering", 14),
    ("master-geological-resources-engineering", 14),
    ("doctoral-geology", 6),
    ("doctoral-geological-engineering", 6),
    ("doctoral-geological-resources-engineering", 6),
)


def build_synthetic_cohort() -> list[SyntheticStudent]:
    students: list[SyntheticStudent] = []
    index = 1
    for profile_id, count in COHORT_PLAN:
        for _ in range(count):
            students.append(
                SyntheticStudent(id=f"GS-{index:03d}", profile_id=profile_id)
            )
            index += 1
    return students


def simulate_cohort(jobs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Assess profile coverage without creating or persisting real student data."""
    job_list = list(jobs)
    profiles = {profile.id: profile for profile in list_student_profiles()}
    students = build_synthetic_cohort()
    by_profile: dict[str, dict[str, Any]] = {}

    for profile in profiles.values():
        matches = [
            (job, evaluate_profile_match(job, profile))
            for job in job_list
        ]
        explicit = [job for job, match in matches if match.level == "explicit"]
        review = [job for job, match in matches if match.level == "review"]
        categories = sorted(
            {job["category"] for job in [*explicit, *review] if job.get("category")}
        )
        by_profile[profile.id] = {
            "profile": {
                "id": profile.id,
                "label": profile.label,
                "degree": profile.degree,
                "major": profile.major,
            },
            "explicit_matches": len(explicit),
            "review_matches": len(review),
            "category_coverage": categories,
            "has_recommendation": bool(explicit or review),
        }

    profile_counts = Counter(student.profile_id for student in students)
    student_results: list[dict[str, Any]] = []
    for student in students:
        profile = profiles[student.profile_id]
        coverage = by_profile[student.profile_id]
        student_results.append(
            {
                "student_id": student.id,
                "profile_id": profile.id,
                "degree": profile.degree,
                "major": profile.major,
                "explicit_matches": coverage["explicit_matches"],
                "review_matches": coverage["review_matches"],
                "category_coverage": coverage["category_coverage"],
                "has_recommendation": coverage["has_recommendation"],
            }
        )

    students_with_explicit = sum(
        profile_counts[profile_id]
        for profile_id, result in by_profile.items()
        if result["explicit_matches"]
    )
    students_with_review_only = sum(
        profile_counts[profile_id]
        for profile_id, result in by_profile.items()
        if not result["explicit_matches"] and result["review_matches"]
    )
    students_without_recommendation = len(students) - (
        students_with_explicit + students_with_review_only
    )
    explicit_job_profile_matches = sum(
        item["explicit_matches"] for item in by_profile.values()
    )
    review_job_profile_matches = sum(
        item["review_matches"] for item in by_profile.values()
    )
    weighted_explicit_opportunities = sum(
        profile_counts[profile_id] * result["explicit_matches"]
        for profile_id, result in by_profile.items()
    )
    return {
        "simulation": "CUPB 地球科学学院匿名 100 人岗位覆盖检查",
        "input_open_jobs": len(job_list),
        "cohort_size": len(students),
        "distribution": [
            {
                "profile_id": profile_id,
                "count": profile_counts[profile_id],
                "label": profiles[profile_id].label,
            }
            for profile_id, _ in COHORT_PLAN
        ],
        "summary": {
            "students_with_explicit_match": students_with_explicit,
            "students_with_review_only": students_with_review_only,
            "students_without_recommendation": students_without_recommendation,
            "explicit_job_profile_matches": explicit_job_profile_matches,
            "review_job_profile_matches": review_job_profile_matches,
            "cohort_weighted_explicit_opportunities": weighted_explicit_opportunities,
        },
        "profiles": [by_profile[item.id] for item in list_student_profiles()],
        "students": student_results,
    }
