from job_hub.source_diversity import build_source_diversity_plan


def _job(source: str, employer: str, province: str, *, status: str = "open") -> dict:
    return {
        "source_id": source,
        "canonical_employer_name": employer,
        "province": province,
        "status": status,
        "publication_status": "student_eligible",
    }


def test_plan_holds_dominant_source_and_prioritizes_empty_enabled_source() -> None:
    jobs = [_job("dominant", "单位A", "北京") for _ in range(8)]
    jobs += [_job("small", "单位B", "山东")]
    plan = build_source_diversity_plan(
        jobs,
        sources=[
            {"id": "dominant", "enabled": True},
            {"id": "small", "enabled": True},
            {"id": "new-official", "enabled": True},
        ],
        target_plan={"segments": [{"id": "energy", "target_jobs": 100, "source_ids": ["dominant", "new-official"]}]},
        max_source_share=0.5,
    )
    assert plan["top_source"]["id"] == "dominant"
    assert plan["source_batches"][0]["source_id"] == "new-official"
    dominant = next(row for row in plan["source_batches"] if row["source_id"] == "dominant")
    assert dominant["gate"] == "hold_dominant_source"


def test_plan_never_counts_pending_or_closed_rows() -> None:
    jobs = [
        _job("official", "单位A", "北京"),
        {**_job("pending", "单位B", "山东"), "publication_status": "pending_evidence"},
        _job("closed", "单位C", "河南", status="expired"),
    ]
    plan = build_source_diversity_plan(
        jobs,
        sources=[{"id": "official", "enabled": True}],
        target_plan={"segments": []},
    )
    assert plan["current_effective_jobs"] == 1
    assert plan["unique_sources"] == 1
    assert plan["gap_jobs"] == 999


def test_unregistered_planned_source_is_not_treated_as_ready() -> None:
    plan = build_source_diversity_plan(
        [],
        sources=[],
        target_plan={"segments": [{"id": "public", "target_jobs": 50, "source_ids": ["unregistered"]}]},
    )
    assert plan["source_batches"][0]["gate"] == "register_official_source"
    assert plan["source_batches"][0]["recommended_batch_size"] == 50
