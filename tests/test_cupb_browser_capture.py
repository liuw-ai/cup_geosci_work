from pathlib import Path
from types import SimpleNamespace

import pytest

import job_hub.cupb_browser_capture as capture_module
from job_hub.cupb_browser_capture import run_cupb_browser_capture


def test_cupb_browser_capture_requires_configured_notices(tmp_path: Path) -> None:
    source = {"homepage_url": "https://career.cup.edu.cn/", "config": {"allowed_hosts": ["career.cup.edu.cn"]}}
    with pytest.raises(ValueError, match="no configured"):
        run_cupb_browser_capture(
            settings=SimpleNamespace(), source=source, output=tmp_path / "snapshot.json"
        )


def test_cupb_announcement_without_role_evidence_is_not_publishable() -> None:
    announcement = SimpleNamespace(
        title="研究院招聘启事",
        employer="某研究院",
        location="",
        qualification_text="博士",
        text="招聘启事正文",
        field_evidence={},
    )
    assert capture_module._publishable_posting(announcement) is False


def test_cupb_row_requires_concrete_scope_and_location() -> None:
    row = SimpleNamespace(
        title="地质工程师",
        employer="某单位",
        location="北京市",
        qualification_text="地质工程；硕士",
        text="地质工程；硕士",
        field_evidence={"evidence_scope": "official_role_section"},
    )
    assert capture_module._publishable_posting(row) is True
    row.field_evidence = {"evidence_scope": "official_notice"}
    assert capture_module._publishable_posting(row) is False


def test_invalid_candidate_does_not_replace_previous_snapshot(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "snapshot.json"
    destination.write_text('[{"external_id":"old"}]', encoding="utf-8")

    def reject_candidate(*_args, **_kwargs):
        raise ValueError("candidate failed official snapshot contract")

    monkeypatch.setattr(capture_module, "_preflight_snapshot", reject_candidate)
    with pytest.raises(ValueError, match="candidate failed"):
        capture_module._promote_snapshot(
            SimpleNamespace(),
            {"config": {}},
            destination,
            [{"external_id": "new"}],
            captured_at="2026-10-08T09:00:00Z",
        )

    assert destination.read_text(encoding="utf-8") == '[{"external_id":"old"}]'
    assert not (tmp_path / "snapshot.json.candidate.tmp").exists()
