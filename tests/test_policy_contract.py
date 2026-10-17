from __future__ import annotations

from job_hub import policy_contract


def test_location_rules_change_publication_policy_digest(tmp_path, monkeypatch) -> None:
    assert "job_hub/locations.py" in policy_contract._POLICY_FILES
    for relative in policy_contract._POLICY_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unchanged")
    monkeypatch.setattr(policy_contract, "_ROOT", tmp_path)

    before = policy_contract.publication_policy_digest()
    (tmp_path / "job_hub/locations.py").write_bytes(b"new city evidence")
    assert policy_contract.publication_policy_digest() != before
