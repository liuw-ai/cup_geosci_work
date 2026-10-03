from job_hub.scorecard import build_scorecard


def _job(source_id: str, status: str = "student_eligible") -> dict:
    return {
        "source_id": source_id,
        "publication_status": status,
        "official_evidence_url": f"https://official.example/{source_id}",
        "field_evidence": {"evidence_scope": "official_job_detail"},
    }


def _inputs(jobs: list[dict]) -> dict:
    return {
        "jobs": jobs,
        "sources": [
            {"id": "a", "enabled": True},
            {"id": "b", "enabled": True},
        ],
        "health_by_id": {
            "a": {"status": "source_active"},
            "b": {"status": "source_active"},
        },
        "crawl_runs": [
            {"id": 4, "source_id": "a", "status": "finished"},
            {"id": 3, "source_id": "a", "status": "finished"},
            {"id": 2, "source_id": "b", "status": "finished"},
            {"id": 1, "source_id": "b", "status": "failed"},
        ],
        "field_completeness": {
            "major_tags": {"rate": 1.0},
            "degree_levels": {"rate": 1.0},
        },
        "deadline_quality": {"known_or_policy": {"rate": 1.0}},
        "location_quality": {"normalized_region": {"rate": 1.0}},
        "quality_gate": {"status": "pass"},
        "province_coverage": [{"published_open_jobs": 1}],
    }


def test_scorecard_excludes_pending_rows_and_requires_two_refreshes() -> None:
    payload = _inputs([_job("a"), _job("b", "pending_evidence")])
    report = build_scorecard(**payload)

    assert report["evidence"]["effective_job_count"] == 2
    assert report["evidence"]["explicit_major_degree_match_count"] == 1
    assert report["evidence"]["two_consecutive_refresh_source_ids"] == ["a"]
    assert report["max_score"] == 100
    assert report["evidence"]["effective_job_target"] == 1000
    assert 0 <= report["score"] <= 100


def test_scorecard_does_not_award_detail_evidence_without_detail_scope() -> None:
    job = _job("a")
    job["field_evidence"] = {"evidence_scope": "official_notice"}
    report = build_scorecard(**_inputs([job]))
    assert report["evidence"]["official_detail_evidence_count"] == 0


def test_scorecard_reports_top_five_source_concentration() -> None:
    jobs = [_job("a") for _ in range(8)]
    jobs.extend(_job(source_id) for source_id in ("b", "c", "d", "e", "f"))
    payload = _inputs(jobs)
    report = build_scorecard(**payload)

    assert report["evidence"]["source_top_share"] == 0.6154
    assert report["evidence"]["source_top_5_share"] == 0.9231
    assert report["evidence"]["employer_top_share"] == 1.0
    assert report["components"]["source_diversity"]["rate"] < 1.0
    assert report["evidence"]["source_balance_factors"]["top_five_sources"] < 1.0
