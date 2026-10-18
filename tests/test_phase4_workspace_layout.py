from __future__ import annotations

from pathlib import Path

from scripts.audit_phase4_workspace_layout import build_report


def test_phase4_layout_requires_canonical_production_files(tmp_path: Path) -> None:
    for relative in (
        "deploy/compose.yaml",
        "deploy/docker/Dockerfile.app",
        "deploy/docker/Dockerfile.browser",
        "job_hub",
        "data",
        "tests",
        "docs/current",
        "runtime",
        "backups",
        "history",
    ):
        path = tmp_path / relative
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)

    report = build_report(tmp_path)

    assert report["production_entrypoint"] == "deploy/compose.yaml"
    assert report["status"] == "pass"
    assert report["current_docs_database_files"] == []


def test_phase4_layout_marks_docs_database_as_review_required(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir(parents=True)
    (tmp_path / "deploy" / "compose.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "deploy" / "docker").mkdir(parents=True)
    (tmp_path / "deploy" / "docker" / "Dockerfile.app").write_text("x", encoding="utf-8")
    (tmp_path / "deploy" / "docker" / "Dockerfile.browser").write_text("x", encoding="utf-8")
    (tmp_path / "docs" / "current").mkdir(parents=True)
    (tmp_path / "docs" / "current" / "accidental.sqlite3").write_bytes(b"not a database")

    report = build_report(tmp_path)

    assert report["status"] == "review_required"
    assert report["current_docs_database_files"] == [
        "docs/current/accidental.sqlite3"
    ]
