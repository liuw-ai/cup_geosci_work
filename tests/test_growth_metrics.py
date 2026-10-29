from job_hub.discovery import discovery_funnel


def _lead(status: str, *, with_url: bool = True, exact: bool = False) -> dict:
    metadata = {}
    if status in {"official_content_verified", "published"}:
        metadata = {
            "title": "某地质岗位",
            "employer": "某地质局",
            "location": "北京市",
            "deadline_date": "2026-12-31",
            "field_evidence": {
                "evidence_scope": "official_html_table_row",
                "岗位": "某地质岗位",
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
            },
        }
        if exact:
            metadata["major_match_mode"] = "exact_four_names"
    return {
        "verification_status": status,
        "official_url": "https://official.example/jobs/1" if with_url else "",
        "official_source_id": "official-source" if with_url else "",
        "official_domain_status": "registered_source_match" if with_url else "unverified",
        "metadata": metadata,
        "lead_fingerprint": status,
        "discovery_source_id": "source",
    }


def test_growth_metrics_keep_each_stage_separate() -> None:
    report = discovery_funnel(
        [
            _lead("candidate", with_url=False),
            _lead("official_url_found"),
            _lead("official_content_verified", exact=True),
            _lead("published"),
        ],
        {"sources": []},
        jobs=[
            {
                "status": "open",
                "publication_status": "student_eligible",
                "province": "北京",
                "country_or_region": "中国大陆",
            },
            {
                "status": "expired",
                "publication_status": "student_eligible",
                "province": "北京",
                "country_or_region": "中国大陆",
            },
        ],
    )

    metrics = report["growth_metrics"]
    assert metrics["metric_contract_version"] == 3
    assert metrics["five_stage_metrics"] == {
        "lead_count": 4,
        "official_url_count": 3,
        "field_complete_count": 2,
        "precise_major_pass_count": 2,
        "current_published_count": 1,
    }
    assert metrics["lead_count"] == 4
    assert metrics["official_url_count"] == 3
    assert metrics["official_content_verified_count"] == 2
    assert metrics["field_complete_count"] == 2
    assert metrics["precise_major_pass_count"] == 2
    assert metrics["student_read_snapshot_count"] == 2
    assert metrics["student_read_domestic_snapshot_count"] == 2
    assert metrics["strict_public_status_count"] == 1
    assert metrics["current_published_count"] == 1
    assert report["source_growth_metrics"]["source"]["current_published_count"] is None
    assert report["source_growth_metrics"]["source"]["student_read_snapshot_count"] is None


def test_growth_metrics_do_not_count_registration_footer_as_official_url() -> None:
    report = discovery_funnel(
        [
            _lead("official_url_found"),
            {
                **_lead("official_url_found"),
                "lead_fingerprint": "filing-link",
                "official_url": "https://beian.miit.gov.cn/",
            },
        ],
        {"sources": []},
    )

    assert report["growth_metrics"]["official_url_count"] == 1


def test_growth_metrics_do_not_count_exam_login_as_job_level_official_url() -> None:
    exam_fee = _lead("official_url_found")
    exam_fee.update(
        {
            "title": "国家公务员考试费用",
            "lead_url": "http://www.huatu.com/z/gkksfy/",
            "official_url": "http://bm.scs.gov.cn/pp/gkweb/core/web/ui/business/home/gkhome.html",
        }
    )
    genuine_notice = _lead("official_url_found")
    genuine_notice.update(
        {
            "lead_fingerprint": "genuine-notice",
            "title": "某地质调查院招聘公告",
            "lead_url": "https://clue.example/2026/notice.html",
        }
    )

    report = discovery_funnel([exam_fee, genuine_notice], {"sources": []})
    assert report["growth_metrics"]["official_url_count"] == 1


def test_growth_metrics_separate_unbound_official_url_candidates() -> None:
    lead = _lead("candidate", with_url=False)
    lead["metadata"] = {
        "official_recovery": {
            "resolved_official_url_candidates": [
                {
                    "url": "https://recruit.example.gov.cn/notice/1",
                    "domain_status": "manual_review_required",
                }
            ]
        }
    }
    report = discovery_funnel([lead], {"sources": []})
    metrics = report["growth_metrics"]
    assert metrics["official_url_candidate_count"] == 1
    assert metrics["raw_official_url_candidate_count"] == 1
    assert metrics["official_url_count"] == 0
    assert metrics["field_complete_count"] == 0


def test_growth_metrics_separate_raw_links_from_actionable_notice_candidates() -> None:
    homepage = _lead("candidate", with_url=False)
    homepage["lead_fingerprint"] = "homepage"
    homepage["metadata"] = {"official_recovery": {
        "resolved_official_url_candidates": [{"url": "https://jobs.example.gov.cn/"}]
    }}
    rejected_notice = _lead("rejected", with_url=False)
    rejected_notice["lead_fingerprint"] = "rejected-notice"
    rejected_notice["metadata"] = {"official_recovery": {
        "resolved_official_url_candidates": [
            {"url": "https://jobs.example.gov.cn/notice/1"}
        ]
    }}
    malformed = _lead("candidate", with_url=False)
    malformed["lead_fingerprint"] = "malformed"
    malformed["metadata"] = {"official_recovery": {
        "resolved_official_url_candidates": [
            {"url": "https://jobs.example.gov.cn/notice/1</a>"}
        ]
    }}

    metrics = discovery_funnel(
        [homepage, rejected_notice, malformed], {"sources": []}
    )["growth_metrics"]
    assert metrics["raw_official_url_candidate_count"] == 2
    assert metrics["official_url_candidate_count"] == 0


def test_growth_metrics_reject_unbound_official_url_and_forged_exact_marker() -> None:
    broad = _lead("official_content_verified")
    broad["lead_fingerprint"] = "broad"
    broad["metadata"]["field_evidence"]["专业范围"] = "地质类"
    broad["metadata"]["major_match_mode"] = "exact_four_names"

    unbound = _lead("official_url_found")
    unbound["lead_fingerprint"] = "unbound"
    unbound["official_domain_status"] = "unverified"
    unbound["official_source_id"] = ""

    report = discovery_funnel([broad, unbound], {"sources": []})
    metrics = report["growth_metrics"]

    assert metrics["official_url_count"] == 1
    assert metrics["official_content_verified_count"] == 1
    assert metrics["field_complete_count"] == 1
    assert metrics["precise_major_pass_count"] == 0
