"""Automatic, fail-closed verification of extracted government position tables.

The attachment parser produces private candidates.  This module checks those
rows against the reviewed government-position ledger without treating a
successful download as proof that a vacancy is publishable. Static ledger
rows require an exact ledger match. Newly discovered official attachments may
use a separate dynamic path, but only when the same official row contains all
required fields and passes the student publication gate. Rolling
``open_until_filled`` rows remain subject to a separate current-availability
confirmation.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from job_hub.locations import normalize_location
from job_hub.matching import extract_deadline, extract_degree_levels
from job_hub.profiles import evaluate_student_publication


_GOVERNMENT_DISCOVERY_ROLES = frozenset(
    {
        "human_resources_or_exam",
        "natural_resources",
        "geology_bureau_or_institute",
        "public_institution_recruitment",
        "civil_service",
    }
)
_GOVERNMENT_DISCOVERY_SOURCE_TYPES = frozenset({"cgs_dynamic_json"})


AUTO_VERIFICATION_VERSION = "government-artifact-verifier-v2"
NOTICE_DEADLINE_MAX_AGE = timedelta(hours=48)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_LOCATOR_NUMBER = re.compile(r"(?:!|第)\s*(\d+)\s*(?:行)?", re.IGNORECASE)


def verify_government_artifacts(
    database: Any,
    manifest: dict[str, Any],
    registry: dict[str, Any],
    *,
    today: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify every extracted ``server_download_pending`` artifact.

    The function is idempotent.  It records a bounded result in artifact
    metadata and promotes only candidates that pass every row-level check to
    ``official_content_verified``.  It never creates a public job and never
    renews a rolling vacancy without a separate availability confirmation.
    """

    target = date.fromisoformat(today) if today else (now.date() if now else date.today())
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    records = list(registry.get("records") or [])
    by_attachment: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (
            str(record.get("source_id") or "").strip(),
            str(record.get("official_attachment_url") or "").strip(),
        )
        if key[0] and key[1]:
            by_attachment.setdefault(key, []).append(record)

    items: list[dict[str, Any]] = []
    for declared in manifest.get("artifacts") or []:
        if str(declared.get("status") or "") != "server_download_pending":
            continue
        item = _verify_one_artifact(database, declared, by_attachment, target, reference)
        items.append(item)

    # Newly discovered official notices do not exist in the versioned static
    # manifest.  They can still be published unattended, but only through a
    # stricter row-self-contained gate: the official source must explicitly
    # opt into attachment discovery and every required field must be present
    # in the same extracted table row.  This keeps the static ledger useful
    # for reviewed government tables without making it a manual bottleneck.
    items.extend(
        _verify_dynamic_discovered_artifacts(database, target, reference)
    )

    counts = {
        "checked": len(items),
        "row_evidence_verified": sum(1 for item in items if item["row_evidence_verified"]),
        "publication_ready": sum(1 for item in items if item["publication_ready"]),
        "blocked": sum(1 for item in items if not item["row_evidence_verified"]),
        "candidates_promoted": sum(int(item["candidates_promoted"]) for item in items),
    }
    return {
        "version": AUTO_VERIFICATION_VERSION,
        "as_of": reference.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        **counts,
        "items": items,
    }


def _verify_dynamic_discovered_artifacts(
    database: Any,
    target: date,
    reference: datetime,
) -> list[dict[str, Any]]:
    """Verify newly discovered government attachments without a static ledger.

    Discovery is opt-in at the source level.  The attachment row itself must
    carry all publication-critical fields; notice-level publisher text is
    never substituted for a missing employer or location.
    """

    results: list[dict[str, Any]] = []
    for artifact in database.iter_source_artifacts():
        metadata = artifact.get("metadata") or {}
        if not isinstance(metadata, dict) or not metadata.get("discovery_only"):
            continue
        if metadata.get("government_artifact_id"):
            continue
        if str(artifact.get("artifact_kind") or "") != "position_table":
            continue
        source = database.get_source(str(artifact.get("source_id") or ""))
        config = source.get("config") if isinstance(source, dict) else None
        if (
            not isinstance(source, dict)
            or not bool(source.get("enabled"))
            or not isinstance(config, dict)
            or not bool(config.get("attachment_discovery_enabled"))
            or not _is_government_discovery_source(source, config)
        ):
            continue
        results.append(
            _verify_dynamic_artifact(database, artifact, source, target, reference)
        )
    return results


def _is_government_discovery_source(
    source: dict[str, Any], config: dict[str, Any]
) -> bool:
    """Keep dynamic auto-publication scoped to public recruitment channels."""

    role = str(config.get("source_role") or "").strip()
    source_type = str(source.get("source_type") or "").strip()
    return role in _GOVERNMENT_DISCOVERY_ROLES or source_type in _GOVERNMENT_DISCOVERY_SOURCE_TYPES


def _verify_dynamic_artifact(
    database: Any,
    artifact: dict[str, Any],
    source: dict[str, Any],
    target: date,
    reference: datetime,
) -> dict[str, Any]:
    source_id = str(artifact.get("source_id") or "").strip()
    artifact_url = str(artifact.get("artifact_url") or "").strip()
    base = {
        "artifact_id": str(artifact.get("id") or ""),
        "source_id": source_id,
        "artifact_url": artifact_url,
        "dynamic_discovery": True,
        "row_evidence_verified": False,
        "publication_ready": False,
        "candidates_promoted": 0,
        "matched_record_ids": [],
        "blocked_reasons": [],
    }
    if str(artifact.get("extraction_status") or "") != "extracted":
        return _persist_result(
            database,
            artifact,
            base | {"blocked_reasons": ["extraction_not_complete"]},
            reference,
        )
    digest = str(artifact.get("content_sha256") or "").strip()
    if not _SHA256.fullmatch(digest):
        return _persist_result(
            database,
            artifact,
            base | {"blocked_reasons": ["content_sha256_missing_or_invalid"]},
            reference,
        )
    if not _official_discovery_urls(source, artifact):
        return _persist_result(
            database,
            artifact,
            base | {"blocked_reasons": ["official_host_allowlist_mismatch"]},
            reference,
        )

    candidates = database.iter_artifact_job_candidates(artifact_id=int(artifact["id"]))
    promoted = 0
    matched_ids: list[str] = []
    reasons: list[str] = []
    for candidate in candidates:
        status = str(candidate.get("review_status") or "")
        if status in {"rejected", "expired"}:
            continue
        candidate_reasons = _dynamic_candidate_reasons(
            candidate, artifact, source, target, reference
        )
        if candidate_reasons:
            reasons.extend(
                f"candidate:{candidate.get('id') or 'unknown'}:{reason}"
                for reason in candidate_reasons
            )
            continue
        matched_ids.append(str(candidate.get("id") or ""))
        if status in {"needs_review", "published"}:
            notice_deadline = _current_notice_deadline_evidence(
                artifact, str(candidate.get("deadline_date") or ""), reference
            )
            evidence_updates = {}
            if notice_deadline and not _value(
                candidate.get("row_cells"),
                "报名截止", "截止日期", "报名截止日期", "截止时间", "报名时间",
            ):
                evidence_updates = {
                    "报名截止依据": "官方公告正文",
                    "报名截止公告链接": notice_deadline["notice_url"],
                    "报名截止公告摘录": notice_deadline["quote"],
                    "报名截止公告SHA256": notice_deadline["notice_sha256"],
                    "报名截止公告核验时间": notice_deadline["captured_at"],
                }
            if status == "needs_review" or evidence_updates:
                database.update_artifact_job_candidate(
                    int(candidate["id"]),
                    {
                        "review_status": (
                            "official_content_verified" if status == "needs_review" else "published"
                        ),
                        "review_note": (
                            "自动核验 government-artifact-dynamic-v1：官方来源、附件哈希、"
                            "岗位行标题、单位、专业、学历、地点、人数、公告或岗位行截止日期和行定位完整。"
                        ),
                        "field_evidence_updates": evidence_updates,
                    },
                )
            if status == "needs_review":
                promoted += 1

    if not matched_ids:
        reasons.append("no_row_satisfied_dynamic_gate")
    result = base | {
        "row_evidence_verified": bool(matched_ids),
        "publication_ready": bool(matched_ids),
        "candidates_promoted": promoted,
        "matched_candidate_ids": matched_ids,
        "blocked_reasons": sorted(set(reasons)),
    }
    return _persist_result(database, artifact, result, reference)


def _official_discovery_urls(source: dict[str, Any], artifact: dict[str, Any]) -> bool:
    config = source.get("config") or {}
    if not isinstance(config, dict):
        return False
    hosts = {
        str(value).strip().lower()
        for value in config.get("allowed_hosts", [])
        if str(value).strip()
    }
    homepage_host = urlparse(str(source.get("homepage_url") or "")).hostname
    if homepage_host:
        hosts.add(homepage_host.lower())
    hosts.update(
        str(value).strip().lower()
        for value in config.get("attachment_allowed_hosts", [])
        if str(value).strip()
    )
    urls = (
        str(artifact.get("parent_url") or "").strip(),
        str(artifact.get("artifact_url") or "").strip(),
    )
    return bool(hosts) and all(
        urlparse(value).scheme in {"http", "https"}
        and (urlparse(value).hostname or "").lower() in hosts
        for value in urls
    )


def _dynamic_candidate_reasons(
    candidate: dict[str, Any],
    artifact: dict[str, Any],
    source: dict[str, Any],
    target: date,
    reference: datetime,
) -> list[str]:
    reasons: list[str] = []
    evidence = candidate.get("field_evidence") or {}
    cells = candidate.get("row_cells") or {}
    if str(candidate.get("extraction_confidence") or "") != "high":
        reasons.append("extraction_confidence_not_high")
    if str(candidate.get("official_page_url") or "").strip() != str(artifact.get("parent_url") or "").strip():
        reasons.append("official_page_url_mismatch")
    if str(evidence.get("artifact_url") or "").strip() != str(artifact.get("artifact_url") or "").strip():
        reasons.append("artifact_url_evidence_mismatch")
    employer_cell = _value(cells, "招聘单位", "用人单位", "单位名称", "招录单位")
    if not employer_cell or _compact(employer_cell) != _compact(candidate.get("employer")):
        reasons.append("employer_not_row_bound")
    row_title = _value(cells, "岗位名称", "岗位", "职位", "招聘岗位", "需求岗位")
    if not row_title or _compact(row_title) != _compact(candidate.get("title")):
        reasons.append("title_not_row_bound")
    major = _value(evidence, "专业范围", "专业要求") or _value(
        cells, "专业", "所需专业", "专业要求", "专业范围", "专业类别", "专业名称"
    )
    degree = _value(evidence, "学历要求", "学历") or _value(
        cells, "学历", "学历学位", "学历层次", "面向对象"
    )
    location_cell = _value(cells, "工作地点", "工作区域", "工作城市", "所在地", "地点")
    location = str(candidate.get("location") or "").strip()
    if not major or not list(candidate.get("major_tags") or []):
        reasons.append("major_missing_or_unclassified")
    if not degree or not extract_degree_levels(degree):
        reasons.append("degree_missing_or_unrecognized")
    if not location or not location_cell or _compact(location_cell) != _compact(location):
        reasons.append("location_not_row_bound")
    if location:
        location_info = normalize_location(location)
        if location_info.get("country_or_region") != "中国大陆":
            reasons.append("mainland_location_not_verified")
    deadline = str(candidate.get("deadline_date") or "").strip()
    row_deadline = _value(
        cells,
        "报名截止",
        "截止日期",
        "报名截止日期",
        "截止时间",
        "报名时间",
    )
    if row_deadline:
        if not _date_text_contains(row_deadline, deadline):
            reasons.append("deadline_row_conflicts_with_candidate")
    elif not _current_notice_deadline_evidence(artifact, deadline, reference):
        reasons.append("deadline_not_bound_to_current_official_notice_or_row")
    try:
        if not deadline or date.fromisoformat(deadline) < target:
            reasons.append("deadline_missing_or_expired")
    except ValueError:
        reasons.append("deadline_invalid")
    headcount = _value(evidence, "招聘人数", "计划人数", "需求人数") or _value(
        cells, "招聘人数", "拟聘人数", "计划数", "人数"
    )
    if _number(headcount) <= 0:
        reasons.append("headcount_missing_or_invalid")
    table_row = _value(evidence, "table_row", "表格定位")
    sheet = str(candidate.get("row_sheet_name") or "").strip()
    row_number = str(candidate.get("row_number") or "").strip()
    if not table_row or not row_number or not sheet:
        reasons.append("row_locator_missing")
    elif _compact(table_row) != _compact(f"{sheet}!{row_number}"):
        reasons.append("row_locator_mismatch")
    if not str(candidate.get("title") or "").strip():
        reasons.append("title_missing")
    publication_evidence = dict(evidence)
    publication_evidence.update(
        {
            "岗位": str(candidate.get("title") or "").strip(),
            "招聘单位": str(candidate.get("employer") or "").strip(),
            "工作地点": location,
        }
    )
    publication = evaluate_student_publication(
        {
            "source_id": candidate.get("source_id"),
            "category": str(source.get("category") or "事业单位与人才引进"),
            "title": candidate.get("title"),
            "location": location,
            "country_or_region": "中国大陆",
            "summary": candidate.get("summary"),
            "description": candidate.get("description"),
            "field_evidence": publication_evidence,
        }
    )
    if publication.status not in {"student_eligible", "unrestricted_eligible"}:
        reasons.append(f"student_gate:{publication.status}")
    return reasons


def _current_notice_deadline_evidence(
    artifact: dict[str, Any], deadline: str, reference: datetime
) -> dict[str, str] | None:
    metadata = artifact.get("metadata") or {}
    evidence = metadata.get("notice_deadline_evidence") if isinstance(metadata, dict) else None
    if not isinstance(evidence, dict):
        return None
    if str(evidence.get("deadline_date") or "") != deadline:
        return None
    if str(evidence.get("notice_url") or "") != str(artifact.get("parent_url") or ""):
        return None
    digest = str(evidence.get("notice_sha256") or "")
    quote = str(evidence.get("quote") or "").strip()
    if not _SHA256.fullmatch(digest) or not quote or extract_deadline(quote) != deadline:
        return None
    try:
        observed = datetime.fromisoformat(
            str(evidence.get("captured_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None
    age = reference.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
    if not timedelta(0) <= age <= NOTICE_DEADLINE_MAX_AGE:
        return None
    return {key: str(value) for key, value in evidence.items()}


def _verify_one_artifact(
    database: Any,
    declared: dict[str, Any],
    records_by_attachment: dict[tuple[str, str], list[dict[str, Any]]],
    target: date,
    reference: datetime,
) -> dict[str, Any]:
    source_id = str(declared.get("source_id") or "").strip()
    artifact_url = str(declared.get("attachment_url") or "").strip()
    artifact = _find_artifact(database, source_id, artifact_url)
    base = {
        "artifact_id": str(declared.get("id") or ""),
        "source_id": source_id,
        "artifact_url": artifact_url,
        "row_evidence_verified": False,
        "publication_ready": False,
        "candidates_promoted": 0,
        "matched_record_ids": [],
        "blocked_reasons": [],
    }
    if artifact is None:
        return _persist_result(database, None, base | {"blocked_reasons": ["artifact_not_registered"]}, reference)
    if str(artifact.get("extraction_status") or "") != "extracted":
        return _persist_result(
            database,
            artifact,
            base | {"blocked_reasons": [f"extraction_status:{artifact.get('extraction_status') or 'unknown'}"]},
            reference,
        )
    digest = str(artifact.get("content_sha256") or "").strip()
    if not _SHA256.fullmatch(digest):
        return _persist_result(database, artifact, base | {"blocked_reasons": ["content_sha256_missing_or_invalid"]}, reference)
    if str(declared.get("evidence_mode") or "registry_bound").strip() == "row_self_contained":
        return _verify_row_self_contained_artifact(
            database,
            artifact,
            declared,
            target,
            reference,
            base,
        )
    declared_records = records_by_attachment.get((source_id, artifact_url), [])
    current_records = [
        record
        for record in declared_records
        if _record_is_current_open(record, target)
    ]
    if not current_records:
        reason = "no_current_verified_open_registry_record"
        return _persist_result(database, artifact, base | {"blocked_reasons": [reason]}, reference)
    metadata = artifact.get("metadata") or {}
    manifest_policy = str(declared.get("deadline_policy") or "").strip()
    manifest_deadline = str(declared.get("deadline_date") or "").strip()
    policy_reasons = _manifest_policy_reasons(current_records, manifest_policy, manifest_deadline)
    if policy_reasons:
        return _persist_result(database, artifact, base | {"blocked_reasons": policy_reasons}, reference)
    candidates = list(database.iter_artifact_job_candidates(artifact_id=int(artifact["id"])))
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    blocked_records: list[dict[str, Any]] = []
    reasons: list[str] = []
    for record in current_records:
        matches, match_reasons = _matches_for_record(
            record,
            candidates,
            artifact,
            declared,
            database=database,
        )
        if len(matches) != 1:
            # A row that is explicitly present in the official attachment but
            # fails the student gate is a valid, auditable exclusion. It must
            # not make unrelated eligible rows in the same table unverifiable.
            # Any existing candidate with the same position code is treated as
            # a field-evidence mismatch instead; that remains fail-closed.
            has_same_code_candidate = any(
                _same_code(candidate, record, artifact=artifact, database=database)
                for candidate in candidates
                if candidate.get("review_status") not in {"rejected", "expired"}
            )
            blocked = None
            if not has_same_code_candidate:
                blocked = _classify_registry_record_block(
                    database,
                    record,
                    artifact,
                    declared,
                )
            if blocked is not None:
                blocked_records.append(blocked)
            else:
                reasons.extend(
                    match_reasons
                    or [f"record_match_ambiguous:{record.get('id') or 'unknown'}:{len(matches)}"]
                )
            continue
        matched.append((record, matches[0]))
    accounted_record_ids = {
        str(record.get("id") or "") for record, _ in matched
    } | {
        str(item.get("record_id") or "") for item in blocked_records
    }
    expected_record_ids = {
        str(record.get("id") or "") for record in current_records
    }
    if reasons or accounted_record_ids != expected_record_ids:
        result = base | {
            "blocked_reasons": sorted(set(reasons)) or ["record_rows_not_exactly_matched"],
            "matched_record_ids": [str(record.get("id") or "") for record, _ in matched],
            "matched_candidate_ids": [],
            "blocked_records": blocked_records,
            "blocked_record_ids": [
                str(item.get("record_id") or "") for item in blocked_records
            ],
        }
        return _persist_result(database, artifact, result, reference)

    promoted = 0
    availability_fresh = _availability_confirmation_is_fresh(
        database, source_id, reference
    )
    for record, candidate in matched:
        fallback_location = _candidate_location(record, candidate, artifact, declared)
        if candidate.get("review_status") == "needs_review":
            changes: dict[str, Any] = {
                "review_status": "official_content_verified",
                "review_note": (
                    f"自动核验 {AUTO_VERIFICATION_VERSION}：附件 SHA-256、岗位代码、单位、"
                    f"专业、学历、地点、人数、期限和行定位均与台账 {record.get('id')} 一致。"
                ),
            }
            if fallback_location and not str(candidate.get("location") or "").strip():
                changes["location"] = fallback_location
                changes["location_evidence"] = (
                    f"官方台账省级地点：{fallback_location}；"
                    "岗位行未列具体市县，来源公告与附件元数据省份一致。"
                )
            database.update_artifact_job_candidate(
                int(candidate["id"]),
                changes,
            )
            promoted += 1
    rolling = any(
        str(record.get("deadline_policy") or "") == "open_until_filled"
        for record, _ in matched
    )
    publication_ready = not rolling or availability_fresh
    if rolling and not availability_fresh:
        reasons.append("current_availability_confirmation_required")
    result = base | {
        "row_evidence_verified": bool(matched),
        "publication_ready": publication_ready if matched else False,
        "candidates_promoted": promoted,
        "matched_record_ids": [str(record.get("id") or "") for record, _ in matched],
        "matched_candidate_ids": [str(candidate.get("id") or "") for _, candidate in matched],
        "blocked_reasons": sorted(
            set(reasons)
            | {
                f"record_blocked:{item.get('record_id') or 'unknown'}:{item.get('reason') or 'unknown'}"
                for item in blocked_records
            }
        ),
        "blocked_records": blocked_records,
        "blocked_record_ids": [
            str(item.get("record_id") or "") for item in blocked_records
        ],
    }
    return _persist_result(database, artifact, result, reference)


def _verify_row_self_contained_artifact(
    database: Any,
    artifact: dict[str, Any],
    declared: dict[str, Any],
    target: date,
    reference: datetime,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Verify rows whose official table is the complete position evidence.

    This path is opt-in per manifest entry. It deliberately does not compare
    employer, title, location, headcount or deadline to a stale static ledger;
    those values must instead be present in the extracted official row itself.
    The row remains subject to source-host, hash, current-date and student
    publication gates.
    """

    source = database.get_source(str(artifact.get("source_id") or ""))
    if not isinstance(source, dict):
        return _persist_result(
            database,
            artifact,
            base | {"blocked_reasons": ["source_not_registered"]},
            reference,
        )
    if not _official_discovery_urls(source, artifact):
        return _persist_result(
            database,
            artifact,
            base | {"blocked_reasons": ["official_host_allowlist_mismatch"]},
            reference,
        )
    candidates = database.iter_artifact_job_candidates(artifact_id=int(artifact["id"]))
    matched: list[dict[str, Any]] = []
    reasons: list[str] = []
    for candidate in candidates:
        status = str(candidate.get("review_status") or "")
        if status in {"rejected", "expired", "published"}:
            continue
        candidate_reasons = _row_self_contained_candidate_reasons(
            candidate, artifact, declared, source, target
        )
        if candidate_reasons:
            reasons.extend(
                f"candidate:{candidate.get('id') or 'unknown'}:{reason}"
                for reason in candidate_reasons
            )
            if (
                status == "official_content_verified"
                and str(candidate.get("review_note") or "").startswith(
                    "自动核验 government-artifact-row-v1"
                )
            ):
                database.update_artifact_job_candidate(
                    int(candidate["id"]),
                    {
                        "review_status": "needs_review",
                        "review_note": (
                            "自动复核阻断：官方岗位表行与当前字段门禁不一致；"
                            + ",".join(candidate_reasons)
                        ),
                    },
                )
            continue
        matched.append(candidate)

    promoted = 0
    for candidate in matched:
        if str(candidate.get("review_status") or "") != "needs_review":
            continue
        database.update_artifact_job_candidate(
            int(candidate["id"]),
            {
                "review_status": "official_content_verified",
                "review_note": (
                    "自动核验 government-artifact-row-v1：官方公告、附件哈希、岗位代码、"
                    "单位、专业、学历、地点、人数、期限和表格定位均来自同一官方岗位行。"
                ),
            },
        )
        promoted += 1

    matched_ids = [str(candidate.get("id") or "") for candidate in matched]
    rolling = str(declared.get("deadline_policy") or "") == "open_until_filled"
    availability_fresh = _availability_confirmation_is_fresh(
        database, str(artifact.get("source_id") or ""), reference
    )
    publication_ready = bool(matched) and (not rolling or availability_fresh)
    if rolling and matched and not availability_fresh:
        reasons.append("current_availability_confirmation_required")
    if not matched:
        reasons.append("no_row_satisfied_self_contained_gate")
    result = base | {
        "evidence_mode": "row_self_contained",
        "row_evidence_verified": bool(matched),
        "publication_ready": publication_ready,
        "candidates_promoted": promoted,
        "matched_record_ids": [],
        "matched_candidate_ids": matched_ids,
        "blocked_reasons": sorted(set(reasons)),
    }
    return _persist_result(database, artifact, result, reference)


def _row_self_contained_candidate_reasons(
    candidate: dict[str, Any],
    artifact: dict[str, Any],
    declared: dict[str, Any],
    source: dict[str, Any],
    target: date,
) -> list[str]:
    """Return every missing or conflicting field for one official table row."""

    reasons: list[str] = []
    evidence = candidate.get("field_evidence") or {}
    cells = candidate.get("row_cells") or {}
    if str(candidate.get("extraction_confidence") or "") != "high":
        reasons.append("extraction_confidence_not_high")
    if str(candidate.get("artifact_content_sha256") or "").strip().lower() != str(
        artifact.get("content_sha256") or ""
    ).strip().lower():
        reasons.append("candidate_artifact_hash_mismatch")
    if str(candidate.get("official_page_url") or "").strip() != str(
        artifact.get("parent_url") or ""
    ).strip():
        reasons.append("official_page_url_mismatch")
    if str(evidence.get("artifact_url") or "").strip() != str(
        artifact.get("artifact_url") or ""
    ).strip():
        reasons.append("artifact_url_evidence_mismatch")

    code = _value(evidence, "职位代码", "岗位代码", "职位编号", "岗位编号") or _value(
        cells, "职位代码", "岗位代码", "职位编号", "岗位编号", "代码"
    )
    title_cell = _value(cells, "岗位名称", "岗位", "职位", "招聘岗位", "需求岗位")
    employer_cell = _value(cells, "招聘单位", "用人单位", "单位名称", "招录单位")
    major = _value(evidence, "专业范围", "专业要求") or _value(
        cells, "专业", "所需专业", "专业要求", "专业范围", "专业类别", "专业名称"
    )
    degree = _value(evidence, "学历要求", "学历") or _value(
        cells, "学历", "学历学位", "学历层次", "面向对象"
    )
    location_cell = _value(cells, "工作地点", "工作区域", "工作城市", "所在地", "地点")
    location = str(candidate.get("location") or "").strip()
    if not code:
        reasons.append("position_code_missing")
    if not title_cell or not str(candidate.get("title") or "").strip():
        reasons.append("title_not_row_bound")
    elif _compact(title_cell) != _compact(candidate.get("title")):
        reasons.append("title_not_row_bound")
    if not employer_cell or _compact(employer_cell) != _compact(candidate.get("employer")):
        reasons.append("employer_not_row_bound")
    if not major or not list(candidate.get("major_tags") or []):
        reasons.append("major_missing_or_unclassified")
    if not degree or not extract_degree_levels(degree):
        reasons.append("degree_missing_or_unrecognized")
    if not location or not location_cell or _compact(location_cell) != _compact(location):
        reasons.append("location_not_row_bound")
    if location and normalize_location(location).get("country_or_region") != "中国大陆":
        reasons.append("mainland_location_not_verified")
    headcount = _value(evidence, "招聘人数", "计划人数", "需求人数") or _value(
        cells, "招聘人数", "拟聘人数", "计划数", "人数"
    )
    if _number(headcount) <= 0:
        reasons.append("headcount_missing_or_invalid")

    sheet = str(candidate.get("row_sheet_name") or "").strip()
    try:
        row_number = int(candidate.get("row_number") or 0)
    except (TypeError, ValueError):
        row_number = 0
    locator = _value(evidence, "table_row", "表格定位")
    if not sheet or row_number < 1 or not locator:
        reasons.append("row_locator_missing")
    elif not (_compact(sheet) in _compact(locator) and str(row_number) in str(locator)):
        reasons.append("row_locator_mismatch")

    policy = str(declared.get("deadline_policy") or "").strip()
    deadline = str(candidate.get("deadline_date") or "").strip()
    row_deadline = _value(
        cells, "报名截止", "截止日期", "报名截止日期", "截止时间", "报名时间"
    ) or _value(evidence, "报名截止", "截止日期", "报名截止日期", "截止时间")
    if policy == "fixed_date":
        if not deadline:
            reasons.append("deadline_missing_or_expired")
        else:
            try:
                if date.fromisoformat(deadline) < target:
                    reasons.append("deadline_missing_or_expired")
            except ValueError:
                reasons.append("deadline_invalid")
        if not row_deadline or not _date_text_contains(row_deadline, deadline):
            reasons.append("deadline_not_row_bound")
        declared_deadline = str(declared.get("deadline_date") or "").strip()
        if declared_deadline and deadline != declared_deadline:
            reasons.append("manifest_deadline_mismatch")
    elif policy == "open_until_filled":
        if deadline:
            try:
                if date.fromisoformat(deadline) < target:
                    reasons.append("deadline_expired")
            except ValueError:
                reasons.append("deadline_invalid")
    else:
        reasons.append("deadline_policy_invalid")

    publication_evidence = dict(evidence)
    publication_evidence.update(
        {
            "岗位": str(candidate.get("title") or "").strip(),
            "招聘单位": str(candidate.get("employer") or "").strip(),
            "工作地点": location,
        }
    )
    publication = evaluate_student_publication(
        {
            "source_id": candidate.get("source_id"),
            "category": str(source.get("category") or "事业单位与人才引进"),
            "title": candidate.get("title"),
            "location": location,
            "country_or_region": "中国大陆",
            "summary": candidate.get("summary"),
            "description": candidate.get("description"),
            "field_evidence": publication_evidence,
        }
    )
    if publication.status not in {"student_eligible", "unrestricted_eligible"}:
        reasons.append(f"student_gate:{publication.status}")
    return sorted(set(reasons))


def _find_artifact(database: Any, source_id: str, artifact_url: str) -> dict[str, Any] | None:
    for artifact in database.iter_source_artifacts(source_id=source_id):
        if (
            str(artifact.get("source_id") or "") == source_id
            and str(artifact.get("artifact_url") or "") == artifact_url
        ):
            return artifact
    return None


def _persist_result(
    database: Any,
    artifact: dict[str, Any] | None,
    result: dict[str, Any],
    reference: datetime,
) -> dict[str, Any]:
    result = dict(result)
    result["checked_at"] = reference.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if artifact is not None:
        metadata = artifact.get("metadata") or {}
        existing = metadata.get("government_auto_verification")
        if not isinstance(existing, dict) or existing != result:
            database.update_source_artifact_processing(
                int(artifact["id"]),
                extraction_status=str(artifact.get("extraction_status") or "extracted"),
                metadata_updates={"government_auto_verification": result},
            )
    return result


def _classify_registry_record_block(
    database: Any,
    record: dict[str, Any],
    artifact: dict[str, Any],
    declared: dict[str, Any],
) -> dict[str, Any] | None:
    """Classify an explicit, non-publishable attachment row as blocked.

    The attachment parser intentionally keeps only student-eligible rows as
    candidates.  For a static registry-bound table that means an out-of-scope
    row can otherwise look like missing evidence and block every valid row in
    the file.  This helper only returns a block when the raw extracted table
    contains exactly one row for the reviewed position code and the shared
    student gate explicitly rejects that row.  Missing rows, ambiguous codes,
    or pending evidence remain hard failures.
    """

    try:
        artifact_id = int(artifact["id"])
        iterator = getattr(database, "iter_source_artifact_rows", None)
        rows = list(iterator(artifact_id)) if callable(iterator) else list(
            database.list_source_artifact_rows(artifact_id)
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    expected_code = _compact(record.get("position_code"))
    if not expected_code:
        return None
    matching_rows = [
        row
        for row in rows
        if _compact(_row_position_code(row)) == expected_code
    ]
    if len(matching_rows) != 1:
        return None
    row = matching_rows[0]
    cells = row.get("cells") or {}
    if not isinstance(cells, dict):
        return None
    title = _value(cells, "岗位名称", "岗位", "职位", "招聘岗位", "需求岗位")
    major = _value(
        cells,
        "专业",
        "所需专业",
        "专业要求",
        "专业范围",
        "专业类别",
        "专业名称",
    )
    degree = _value(cells, "学历", "学历要求", "学历学位", "学历层次", "面向对象")
    location = _value(cells, "工作地点", "工作区域", "工作城市", "所在地", "地点")
    condition = _value(
        cells,
        "岗位条件",
        "岗位要求",
        "任职条件",
        "任职要求",
        "任职资格",
        "资格条件",
        "招聘条件",
        "应聘条件",
        "工作经验",
        "工作年限",
    )
    metadata = artifact.get("metadata") or {}
    field_evidence: dict[str, Any] = {
        "evidence_scope": "official_attachment_row",
        "岗位": title or str(record.get("title") or "").strip(),
        "专业范围": major,
        "学历要求": degree,
        "工作地点": location,
        "职位代码": str(record.get("position_code") or "").strip(),
        "table_row": f"{row.get('sheet_name')}!{row.get('row_number')}",
        "artifact_url": str(artifact.get("artifact_url") or "").strip(),
        "row_text": str(row.get("row_text") or "")[:2000],
    }
    if condition:
        field_evidence["岗位资格条件"] = condition
    if str(metadata.get("position_type") or "").strip():
        field_evidence["政府岗位类型"] = str(metadata["position_type"]).strip()
    decision = evaluate_student_publication(
        {
            "source_id": record.get("source_id"),
            "category": str(metadata.get("category") or "事业单位与人才引进"),
            "title": title or record.get("title"),
            "location": location or record.get("location"),
            "country_or_region": "中国大陆",
            "summary": str(row.get("row_text") or ""),
            "description": str(row.get("row_text") or ""),
            "field_evidence": field_evidence,
        }
    )
    if decision.status != "out_of_scope":
        return None
    return {
        "record_id": str(record.get("id") or ""),
        "position_code": str(record.get("position_code") or "").strip(),
        "row_locator": f"{row.get('sheet_name')}!{row.get('row_number')}",
        "reason": str(decision.label or decision.reason or "student_gate_rejected"),
        "detail": str(decision.reason or "").strip(),
        "publication_status": str(decision.status),
    }


def _row_position_code(row: dict[str, Any]) -> str:
    cells = row.get("cells") or {}
    code = _value(cells, "职位代码", "岗位代码", "职位编号", "岗位编号", "代码", "序号")
    if code:
        return code
    text = str(row.get("row_text") or "")
    match = re.search(
        r"(?:岗位|职位)(?:代码|编号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_.\-/]{1,39})",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _matches_for_record(
    record: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    artifact: dict[str, Any],
    declared: dict[str, Any],
    *,
    database: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    matches: list[dict[str, Any]] = []
    reasons: list[str] = []
    for candidate in candidates:
        if candidate.get("review_status") in {"rejected", "expired"}:
            continue
        candidate = _hydrate_candidate_from_registry(
            database,
            record,
            candidate,
            artifact,
            declared,
        )
        checks = _candidate_checks(
            record,
            candidate,
            artifact,
            declared,
            database=database,
        )
        if all(checks.values()):
            matches.append(candidate)
        else:
            failed = ",".join(key for key, value in checks.items() if not value)
            if (
                database is not None
                and candidate.get("review_status") == "official_content_verified"
                and str(candidate.get("review_note") or "").startswith("自动核验 government-artifact-verifier-")
                and _same_code(candidate, record, artifact=artifact, database=database)
            ):
                database.update_artifact_job_candidate(
                    int(candidate["id"]),
                    {
                        "review_status": "needs_review",
                        "review_note": (
                            "自动复核阻断：先前自动核验记录与当前官方附件字段不一致；"
                            f"需重新核对 {failed}。"
                        ),
                    },
                )
            if failed and _same_code(candidate, record, artifact=artifact, database=database):
                reasons.append(f"record_field_mismatch:{record.get('id') or 'unknown'}:{failed}")
    return matches, reasons


def _hydrate_candidate_from_registry(
    database: Any | None,
    record: dict[str, Any],
    candidate: dict[str, Any],
    artifact: dict[str, Any],
    declared: dict[str, Any],
) -> dict[str, Any]:
    """Complete a uniquely coded, partially extracted row from the ledger.

    Official PDFs sometimes flatten merged cells into text-only rows.  When a
    row still contains the exact position code, the reviewed position ledger
    can provide the missing title, employer and location. The fallback is
    deliberately narrow: it requires the record's explicit
    ``registry_province`` policy, matching official page URL and a code match.
    """

    if database is None:
        return candidate
    if str(record.get("location_evidence_policy") or "row_exact") != "registry_province":
        return candidate
    if str(candidate.get("official_page_url") or "").strip() != str(artifact.get("parent_url") or "").strip():
        return candidate
    evidence = dict(candidate.get("field_evidence") or {})
    cells = candidate.get("row_cells") or {}
    code, binding = _position_code_binding(database, record, candidate, artifact)
    if _compact(code) != _compact(record.get("position_code")):
        return candidate
    explicit_employer = _value(cells, "招聘单位", "用人单位", "单位名称", "招录单位")
    explicit_title = _value(cells, "岗位名称", "岗位", "职位", "招聘岗位", "需求岗位")
    explicit_major = _value(cells, "专业", "专业要求", "所需专业", "岗位所需专业", "专业及代码", "专业条件要求")
    evidence_major = _value(evidence, "专业范围", "专业要求")
    expected_major = record.get("major_requirement")
    if any(
        value and not _government_major_requirement_matches(record, value, expected_major)
        for value in (explicit_major, evidence_major)
    ):
        return candidate
    explicit_degree = _value(cells, "学历", "学历要求", "学历学位", "学历及学位")
    if explicit_degree and set(extract_degree_levels(explicit_degree)) != set(
        extract_degree_levels(str(record.get("degree_requirement") or ""))
    ):
        return candidate
    explicit_location = _value(evidence, "工作地点", "工作地点依据", "地点") or _value(
        cells, "工作地点", "工作区域", "工作城市", "所在地", "地点"
    )
    if explicit_employer and _compact(explicit_employer) != _compact(record.get("employer")):
        return candidate
    if explicit_title and explicit_title != "职位表岗位（待核验）" and _compact(explicit_title) != _compact(record.get("title")):
        return candidate
    if explicit_location and _compact(explicit_location) != _compact(record.get("location")):
        return candidate
    title = str(record.get("title") or "").strip()
    employer = str(record.get("employer") or "").strip()
    location = str(record.get("location") or "").strip()
    if not title or not employer or not location:
        return candidate
    observed_headcount = _headcount_evidence(database, record, candidate, artifact)
    updates = {
        "title": title,
        "employer": employer,
        "location": location,
        "degree_levels": list(extract_degree_levels(str(record.get("degree_requirement") or ""))),
        "major_tags": list(candidate.get("major_tags") or []) or [
            str(record.get("major_requirement") or "").strip()
        ],
        "location_evidence": (
            "官方台账省级地点：岗位代码唯一匹配已核验官方职位台账；"
            "记录明确允许 registry_province，地点使用台账岗位地点，"
            "未使用机构主页地址推断。"
        ),
        "field_evidence_updates": {
            "岗位": title,
            "职位代码": str(record.get("position_code") or "").strip(),
            "专业范围": str(record.get("major_requirement") or "").strip(),
            "学历要求": str(record.get("degree_requirement") or "").strip(),
            "工作地点依据": (
                "官方台账省级地点：岗位代码唯一匹配已核验官方职位台账；"
                "记录明确允许 registry_province，未使用机构主页地址推断。"
            ),
            "registry_record_id": str(record.get("id") or "").strip(),
            "registry_hydrated": "true",
            "artifact_url": str(artifact.get("artifact_url") or "").strip(),
            "position_code_binding": binding,
        },
    }
    if observed_headcount:
        updates["field_evidence_updates"]["招聘人数"] = observed_headcount
    hydrated = dict(candidate)
    hydrated.update({key: value for key, value in updates.items() if key != "field_evidence_updates"})
    hydrated["field_evidence"] = dict(evidence)
    hydrated["field_evidence"].update(updates["field_evidence_updates"])
    hydrated["extraction_confidence"] = "high"
    # A published artifact row is immutable except for evidence refreshes.
    # Registry hydration still updates the in-memory candidate used for the
    # current verification, but must not attempt to rewrite its public fields.
    persisted_updates = updates
    if str(candidate.get("review_status") or "") == "published":
        persisted_updates = {
            "field_evidence_updates": updates["field_evidence_updates"],
        }
    database.update_artifact_job_candidate(int(candidate["id"]), persisted_updates)
    return hydrated


def _candidate_checks(
    record: dict[str, Any],
    candidate: dict[str, Any],
    artifact: dict[str, Any],
    declared: dict[str, Any],
    *,
    database: Any | None = None,
) -> dict[str, bool]:
    evidence = candidate.get("field_evidence") or {}
    cells = candidate.get("row_cells") or {}
    code, _ = _position_code_binding(database, record, candidate, artifact)
    employer = str(candidate.get("employer") or _value(cells, "招聘单位", "用人单位") or "").strip()
    major = _value(evidence, "专业范围", "专业要求") or _value(cells, "专业", "所需专业", "专业要求")
    row_major = _value(cells, "专业", "专业要求", "所需专业", "岗位所需专业", "专业及代码", "专业条件要求")
    degree = _value(evidence, "学历要求", "学历") or _value(cells, "学历", "学历学位")
    location = _candidate_location(record, candidate, artifact, declared)
    headcount = _headcount_evidence(database, record, candidate, artifact)
    table_row = _value(evidence, "table_row", "表格定位")
    candidate_deadline = str(candidate.get("deadline_date") or "").strip()
    expected_deadline = str(record.get("deadline_date") or "").strip()
    manifest_deadline = str(declared.get("deadline_date") or "").strip()
    locator_matches = _locator_matches(table_row, record)
    if not locator_matches:
        locator_matches = _code_bound_locator_matches(
            database,
            record,
            candidate,
            artifact,
            declared,
        )
    return {
        "official_page_url": str(candidate.get("official_page_url") or "").strip()
        == str(artifact.get("parent_url") or "").strip(),
        "artifact_url": str(evidence.get("artifact_url") or "").strip()
        == str(artifact.get("artifact_url") or "").strip(),
        "position_code": _compact(code) == _compact(record.get("position_code")),
        "employer": _compact(employer) == _compact(record.get("employer")),
        "major_requirement": _government_major_requirement_matches(
            record, major, record.get("major_requirement")
        )
        and (
            not row_major
            or _government_major_requirement_matches(
                record, row_major, record.get("major_requirement")
            )
        ),
        "degree_requirement": set(extract_degree_levels(degree))
        == set(extract_degree_levels(str(record.get("degree_requirement") or ""))),
        "location": bool(location) and _compact(location) == _compact(record.get("location")),
        "headcount": _number(headcount) == int(record.get("headcount") or 0),
        "deadline": _deadline_matches(
            candidate_deadline,
            expected_deadline,
            manifest_deadline,
            str(declared.get("deadline_policy") or ""),
        ),
        "locator": locator_matches,
        "extraction_confidence": (
            str(candidate.get("extraction_confidence") or "") == "high"
            or (
                str(evidence.get("registry_hydrated") or "").lower() == "true"
                and str(evidence.get("position_code_binding") or "").strip()
            )
        ),
    }


def _candidate_location(
    record: dict[str, Any],
    candidate: dict[str, Any],
    artifact: dict[str, Any],
    declared: dict[str, Any],
) -> str:
    """Return row location, or an explicitly declared province fallback.

    A missing row location remains a hard failure by default.  The only
    exception is a registry record that explicitly opts into
    ``registry_province`` and whose province is repeated by the manifest and
    extracted artifact metadata.  This keeps province filtering useful for
    official tables that intentionally omit a city while preventing a generic
    employer or notice address from being guessed as a work location.
    """
    evidence = candidate.get("field_evidence") or {}
    cells = candidate.get("row_cells") or {}
    observed = str(candidate.get("location") or _value(evidence, "工作地点", "工作地点依据") or "").strip()
    if observed:
        return observed
    if str(record.get("location_evidence_policy") or "row_exact") != "registry_province":
        return ""
    province = str(record.get("province") or "").strip()
    registry_location = str(record.get("location") or "").strip()
    declared_province = str(declared.get("province") or "").strip()
    metadata = artifact.get("metadata") or {}
    artifact_province = str(metadata.get("province") or "").strip()
    if not province or province not in registry_location:
        return ""
    if declared_province != province or artifact_province != province:
        return ""
    # If a row contains any non-empty location-like cell, do not replace it
    # with the registry value. Such a conflict requires a new source review.
    row_location = _value(
        cells,
        "工作地点",
        "工作区域",
        "工作城市",
        "所在地",
        "地点",
    )
    if row_location:
        return ""
    return registry_location


def _record_is_current_open(record: dict[str, Any], target: date) -> bool:
    if str(record.get("record_status") or "") != "verified_open":
        return False
    if str(record.get("match_status") or "") not in {"explicit_match", "unrestricted_match"}:
        return False
    deadline = str(record.get("deadline_date") or "").strip()
    return not deadline or date.fromisoformat(deadline) >= target


def _manifest_policy_reasons(records: list[dict[str, Any]], policy: str, deadline: str) -> list[str]:
    reasons: list[str] = []
    for record in records:
        if str(record.get("deadline_policy") or "") != policy:
            reasons.append(
                f"deadline_policy_mismatch:{record.get('id') or 'unknown'}:{policy}->{record.get('deadline_policy') or ''}"
            )
        expected = str(record.get("deadline_date") or "")
        if expected != deadline and policy == "fixed_date":
            reasons.append(
                f"deadline_date_mismatch:{record.get('id') or 'unknown'}:{deadline}->{expected}"
            )
    return reasons


def _availability_confirmation_is_fresh(database: Any, source_id: str, reference: datetime) -> bool:
    for item in database.list_government_source_verifications():
        if str(item.get("source_id") or "") != source_id:
            continue
        raw = str(item.get("availability_confirmed_at") or "").strip()
        if not raw:
            continue
        try:
            checked = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        age_hours = (reference - checked.astimezone(timezone.utc)).total_seconds() / 3600
        if item.get("status") == "verified" and 0 <= age_hours <= 48:
            return True
    return False


def _same_code(
    candidate: dict[str, Any],
    record: dict[str, Any],
    *,
    artifact: dict[str, Any] | None = None,
    database: Any | None = None,
) -> bool:
    observed, _ = _position_code_binding(database, record, candidate, artifact)
    return _compact(observed) == _compact(record.get("position_code"))


def _position_code_binding(
    database: Any | None,
    record: dict[str, Any],
    candidate: dict[str, Any],
    artifact: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return the expected code and how the official row supplied it.

    PDF text extraction can put a position code on the next visual line.  The
    adjacent-row fallback is deliberately narrower than fuzzy title matching:
    it requires an explicit registry policy, the same artifact, the same page,
    a one-row distance, and exactly one artifact location containing the code.
    """

    expected = str(record.get("position_code") or "").strip()
    expected_compact = _compact(expected)
    if not expected_compact:
        return "", ""
    evidence = candidate.get("field_evidence") or {}
    cells = candidate.get("row_cells") or {}
    hydrated = str(evidence.get("registry_hydrated") or "").lower() == "true"
    explicit_cells = _value(
        cells,
        "职位代码",
        "岗位代码",
        "职位编号",
        "岗位编号",
        "代码",
        # Government tables often use a serial-number column as the only
        # stable row identifier.  It is accepted only for a registry record
        # whose position_code explicitly equals that serial value; it is not
        # fuzzy title matching or a source-wide fallback.
        "序号",
    )
    if explicit_cells:
        return explicit_cells, "same-row-explicit"
    explicit_evidence = _value(
        evidence,
        "职位代码",
        "岗位代码",
        "职位编号",
        "岗位编号",
    )
    # A hydrated code is derived evidence. Re-read the raw row first so a
    # later run cannot turn an adjacent-row binding into a same-row claim.
    if explicit_evidence and not hydrated:
        return explicit_evidence, "same-row-explicit"
    row_text = " ".join(
        str(candidate.get(key) or "")
        for key in ("row_text", "summary", "description")
    )
    if _text_contains_code(row_text, expected):
        return expected, "same-row-text"
    if database is None or artifact is None:
        return "", ""
    if str(record.get("location_evidence_policy") or "row_exact") != "registry_province":
        return "", ""
    if str(candidate.get("official_page_url") or "").strip() != str(artifact.get("parent_url") or "").strip():
        return "", ""
    artifact_url = str(evidence.get("artifact_url") or "").strip()
    if artifact_url != str(artifact.get("artifact_url") or "").strip():
        return "", ""
    sheet = str(candidate.get("row_sheet_name") or "").strip()
    try:
        row_number = int(candidate.get("row_number") or 0)
    except (TypeError, ValueError):
        row_number = 0
    if not sheet or row_number < 1:
        return "", ""
    locations: set[tuple[str, int]] = set()
    for row in database.iter_source_artifact_rows(int(artifact["id"])):
        text = " ".join(
            [
                str(row.get("row_text") or ""),
                " ".join(str(value) for value in (row.get("cells") or {}).values()),
            ]
        )
        if _text_contains_code(text, expected):
            try:
                number = int(row.get("row_number") or 0)
            except (TypeError, ValueError):
                continue
            locations.add((str(row.get("sheet_name") or ""), number))
    adjacent = [
        location
        for location in locations
        if location[0] == sheet and abs(location[1] - row_number) <= 1
    ]
    if len(adjacent) != 1:
        return "", ""
    bound_sheet, bound_row = adjacent[0]
    if bound_row == row_number:
        return expected, "same-row-artifact-text"
    return expected, f"adjacent-row:{bound_sheet}!{bound_row}"


def _code_bound_locator_matches(
    database: Any | None,
    record: dict[str, Any],
    candidate: dict[str, Any],
    artifact: dict[str, Any],
    declared: dict[str, Any],
) -> bool:
    """Use a unique official code as a locator supplement when labels differ."""

    if str(record.get("location_evidence_policy") or "row_exact") != "registry_province":
        return False
    if str(candidate.get("official_page_url") or "").strip() != str(artifact.get("parent_url") or "").strip():
        return False
    evidence = candidate.get("field_evidence") or {}
    if str(evidence.get("artifact_url") or "").strip() != str(artifact.get("artifact_url") or "").strip():
        return False
    expected = str(record.get("position_code") or "").strip()
    observed, binding = _position_code_binding(database, record, candidate, artifact)
    if _compact(observed) != _compact(expected) or not binding:
        return False
    # The manifest must reference the same official attachment as the row.
    return str(declared.get("attachment_url") or "").strip() == str(artifact.get("artifact_url") or "").strip()


def _headcount_evidence(
    database: Any | None,
    record: dict[str, Any],
    candidate: dict[str, Any],
    artifact: dict[str, Any],
) -> str:
    """Read headcount only from extracted attachment cells.

    Registry hydration may fill a missing title or employer, but it must never
    manufacture the number of vacancies.  For flattened PDFs, the only extra
    source allowed is the unique adjacent row already proven by
    ``_position_code_binding``.
    """

    evidence = candidate.get("field_evidence") or {}
    cells = candidate.get("row_cells") or {}
    row_explicit = _value(cells, "招聘人数", "拟聘人数", "计划数", "招聘计划数")
    explicit = _value(evidence, "招聘人数", "计划人数", "需求人数")
    generic_values = _generic_numeric_cells(cells)
    if generic_values:
        return generic_values[0]
    if row_explicit:
        return row_explicit
    if explicit and str(evidence.get("registry_hydrated") or "").lower() != "true":
        return explicit
    if database is None:
        return explicit if str(evidence.get("registry_hydrated") or "").lower() != "true" else ""
    expected = str(record.get("position_code") or "").strip()
    observed_code, binding = _position_code_binding(database, record, candidate, artifact)
    if _compact(observed_code) != _compact(expected) or not binding:
        return ""
    sheet = str(candidate.get("row_sheet_name") or "").strip()
    try:
        row_number = int(candidate.get("row_number") or 0)
    except (TypeError, ValueError):
        row_number = 0
    if not sheet or row_number < 1:
        return ""
    values: list[str] = []
    for row in database.iter_source_artifact_rows(int(artifact["id"])):
        if str(row.get("sheet_name") or "") != sheet:
            continue
        try:
            candidate_row_number = int(row.get("row_number") or 0)
        except (TypeError, ValueError):
            continue
        if abs(candidate_row_number - row_number) > 1:
            continue
        row_text = " ".join(
            [
                str(row.get("row_text") or ""),
                " ".join(str(value) for value in (row.get("cells") or {}).values()),
            ]
        )
        if not _text_contains_code(row_text, expected):
            continue
        values.extend(_generic_numeric_cells(row.get("cells") or {}))
    return values[0] if len(set(values)) == 1 else ""


def _generic_numeric_cells(cells: Any) -> list[str]:
    """Return small numeric values from unlabeled PDF columns only."""

    if not isinstance(cells, dict):
        return []
    values: list[str] = []
    for key, value in cells.items():
        if not re.fullmatch(r"列\s*\d+", str(key or "")):
            continue
        text = str(value or "").strip().replace(",", "")
        if re.fullmatch(r"\d{1,4}(?:\.0+)?", text):
            values.append(str(int(float(text))))
    return values


def _text_contains_code(text: Any, expected: Any) -> bool:
    compact_text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))
    compact_expected = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(expected or "")))
    if not compact_text or not compact_expected:
        return False
    start = 0
    while True:
        index = compact_text.find(compact_expected, start)
        if index < 0:
            return False
        before = compact_text[index - 1] if index else ""
        end = index + len(compact_expected)
        after = compact_text[end] if end < len(compact_text) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        start = end


def _locator_matches(locator: Any, record: dict[str, Any]) -> bool:
    observed = _compact(locator)
    expected = _compact(record.get("evidence_locator"))
    if not observed or not expected:
        return False
    if observed == expected or observed in expected or expected in observed:
        return True
    observed_number = _LOCATOR_NUMBER.search(str(locator or ""))
    expected_number = _LOCATOR_NUMBER.search(str(record.get("evidence_locator") or ""))
    return bool(observed_number and expected_number and observed_number.group(1) == expected_number.group(1))


def _deadline_matches(candidate: str, expected: str, manifest: str, policy: str) -> bool:
    if expected:
        return candidate == expected or (not candidate and manifest == expected)
    # A rolling notice may print its administrative validity date in the
    # extracted row even though the ledger intentionally models availability
    # as ``open_until_filled``.  That date is retained as evidence, but it is
    # not compared to an invented fixed deadline.
    return not candidate or policy == "open_until_filled"


def _requirement_matches(observed: Any, expected: Any) -> bool:
    left = _compact(observed)
    right = _compact(expected)
    if not left or not right:
        return False
    if left == right:
        return True
    observed_pairs = _coded_major_pairs(observed)
    expected_pairs = _coded_major_pairs(expected)
    return bool(observed_pairs and expected_pairs and observed_pairs == expected_pairs)


# A flattened official PDF can preserve only a subset of the major condition
# on the row containing the position code.  These terms are used solely for
# matching a government ledger row to that same attachment; they are not part
# of enterprise/student-facing major matching.
_REGISTRY_MAJOR_TERMS = tuple(
    sorted(
        {
            "地质资源与地质工程",
            "资源勘查工程",
            "地质工程",
            "地质学",
            "资源与环境",
            "地质勘查",
            "矿产普查与勘查",
            "矿产普查与勘探",
            "地球探测与信息技术",
            "矿物学",
            "岩石学",
            "矿床学",
            "地球化学",
            "构造地质学",
            "矿产勘查",
            "测绘",
        },
        key=len,
        reverse=True,
    )
)
_REGISTRY_MAJOR_EXPANSIONS = {
    "地质资源与地质工程": frozenset(
        {
            "地质资源与地质工程",
            "地质工程",
            "矿产普查与勘查",
            "矿产普查与勘探",
            "地球探测与信息技术",
        }
    ),
    "地质学": frozenset(
        {"地质学", "矿物学", "岩石学", "矿床学", "地球化学", "构造地质学"}
    ),
}
_REGISTRY_MAJOR_NOISE = re.compile(
    r"专业|方向|类别|代码|要求|包括|包含|含|等|相关|以及|及|和|或|不限|任选|以上|以下"
)


def _government_major_requirement_matches(
    record: dict[str, Any], observed: Any, expected: Any
) -> bool:
    """Match a government row, allowing only an auditable major subset.

    The fallback is deliberately unavailable to enterprise rows.  It is only
    enabled for a reviewed ``registry_province`` record, after the caller has
    established the same official notice/attachment and position-code binding.
    Every observed recognized term must be covered by the ledger requirement;
    residual Chinese text is rejected so an unrelated discipline cannot ride
    along with one matching token.
    """

    if _requirement_matches(observed, expected):
        return True
    if str(record.get("location_evidence_policy") or "row_exact") != "registry_province":
        return False
    observed_terms = _registry_major_terms_in(observed)
    expected_terms = _registry_major_terms_in(expected)
    if not observed_terms or not expected_terms:
        return False
    allowed = set(expected_terms)
    for term in expected_terms:
        allowed.update(_REGISTRY_MAJOR_EXPANSIONS.get(term, ()))
    if not observed_terms.issubset(allowed):
        return False
    residual = unicodedata.normalize("NFKC", str(observed or ""))
    for term in _REGISTRY_MAJOR_TERMS:
        residual = residual.replace(term, "")
    residual = re.sub(r"[0-9A-Za-z\s、,，；;:：()（）/／.。\-—_]+", "", residual)
    residual = _REGISTRY_MAJOR_NOISE.sub("", residual)
    return not re.search(r"[\u4e00-\u9fff]", residual)


def _registry_major_terms_in(value: Any) -> frozenset[str]:
    """Extract non-overlapping reviewed terms without substring aliases."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    selected: list[tuple[int, int, str]] = []
    for term in _REGISTRY_MAJOR_TERMS:
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            end = index + len(term)
            if not any(index < other_end and end > other_start for other_start, other_end, _ in selected):
                selected.append((index, end, term))
            start = end
    return frozenset(term for _, _, term in selected)


def _coded_major_pairs(value: Any) -> frozenset[tuple[str, str]] | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    parts = [
        part.strip()
        for part in re.split(r"[\n\r;；]+|[、,，](?=\s*\d{4,8}[A-Za-z]?\s*)", text)
        if part.strip()
    ]
    if not parts:
        return None
    pairs: list[tuple[str, str]] = []
    for part in parts:
        match = re.fullmatch(
            r"(?:\(?\d{1,2}\)[.、]?\s*|\d{1,2}[.、]\s*)?"
            r"(\d{4,8}[A-Za-z]?)\s*([\u4e00-\u9fff、,，]+)",
            part,
        )
        if not match:
            return None
        pairs.append((match.group(1), match.group(2)))
    return frozenset(pairs) if len(set(pairs)) == len(parts) else None


def _number(value: Any) -> int:
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"\d+(?:\.0+)?", text)
    return int(float(match.group(0))) if match else 0


def _date_text_contains(observed: Any, expected_iso: str) -> bool:
    """Match an ISO candidate date to common official Chinese date text."""

    expected = str(expected_iso or "").strip()
    if not expected:
        return False
    compact_observed = _compact(observed)
    compact_expected = _compact(expected)
    if compact_expected in compact_observed:
        return True
    try:
        expected_date = date.fromisoformat(expected)
    except ValueError:
        return False
    patterns = (
        f"{expected_date.year}年{expected_date.month}月{expected_date.day}日",
        f"{expected_date.year}/{expected_date.month}/{expected_date.day}",
        f"{expected_date.year}.{expected_date.month}.{expected_date.day}",
    )
    return any(_compact(pattern) in compact_observed for pattern in patterns)


def _value(mapping: Any, *keys: str) -> str:
    if not isinstance(mapping, dict):
        return ""
    normalized = {_compact(key): str(value).strip() for key, value in mapping.items() if str(value).strip()}
    for key in keys:
        value = normalized.get(_compact(key))
        if value:
            return value
    return ""


def _compact(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"\s+", "", text)
    return text
