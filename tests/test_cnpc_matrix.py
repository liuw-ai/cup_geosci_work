from pathlib import Path

import pytest

from job_hub.cnpc_matrix import (
    CnpcMatrixError,
    audit_cnpc_snapshot,
    build_cnpc_matrix_report,
    load_cnpc_snapshot,
    load_cnpc_unit_matrix,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = PROJECT_ROOT / "data" / "verified" / "cnpc-geoscience-20260924.json"


def test_cnpc_matrix_binds_every_verified_snapshot_row() -> None:
    report = build_cnpc_matrix_report(snapshot_path=SNAPSHOT)

    assert report["summary"]["unit_count"] == 13
    assert report["summary"]["units_with_sample_job"] == 11
    assert report["summary"]["snapshot_rows"] == 20
    assert report["summary"]["snapshot_rows_bound"] == 20
    assert report["summary"]["explicit_major_matches"] == 20
    assert report["summary"]["snapshot_contract_passed"] is True
    assert report["audit"]["unmapped_jobs"] == []


def test_cnpc_matrix_rejects_non_official_evidence() -> None:
    units = load_cnpc_unit_matrix()
    rows = load_cnpc_snapshot(SNAPSHOT)
    rows[0]["official_evidence_url"] = "https://example.com/fake"

    audit = audit_cnpc_snapshot(rows, units)

    assert audit["all_rows_bound"] is False
    assert audit["invalid_evidence"] == [
        {
            "external_id": rows[0]["external_id"],
            "field": "official_evidence_url",
            "value": "https://example.com/fake",
        }
    ]


def test_cnpc_matrix_rejects_missing_backup_entry(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        '{"records":[{"system":"中国石油","id":"x","organization":"测试单位",'
        '"parent_organization":"中国石油天然气集团有限公司","organization_role":"upstream_operator",'
        '"official_url":"https://zhaopin.cnpc.com.cn/","backup_urls":[],'
        '"channel_type":"official_recruitment_system","status":"official_identity_only"}]}',
        encoding="utf-8",
    )

    with pytest.raises(CnpcMatrixError, match="备用官方入口"):
        load_cnpc_unit_matrix(queue_path)
