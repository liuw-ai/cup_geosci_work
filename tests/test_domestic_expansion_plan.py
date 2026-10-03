from job_hub.domestic_expansion import domestic_expansion_action_plan


def test_action_plan_prioritizes_verified_sample_for_repeat_refresh() -> None:
    queue = {
        "records": [
            {"id": "identity", "system": "中国石油", "organization": "A", "status": "official_identity_only", "source_id": None},
            {"id": "sample", "system": "中国石化", "organization": "B", "status": "official_job_sample_verified", "source_id": "sinopec-b"},
            {"id": "blocked", "system": "事业编", "organization": "C", "status": "access_limited", "source_id": None},
        ]
    }
    plan = domestic_expansion_action_plan(queue)
    assert plan["tasks"][0]["id"] == "sample"
    assert plan["tasks"][0]["gate"] == "repeat_server_refresh"
    assert plan["by_gate"]["find_job_sample"] == 1
