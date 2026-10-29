from __future__ import annotations

import os

from job_hub.config import PROJECT_ROOT, Settings, load_project_env


def test_project_env_loads_missing_values_without_overriding_process_env(
    tmp_path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# local values\nHTTP_TRANSPORT_MODE=direct\nAPP_DATA_DIR='runtime-local'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HTTP_TRANSPORT_MODE", raising=False)
    monkeypatch.setenv("APP_DATA_DIR", "runtime-process")

    loaded = load_project_env(env_path)

    assert loaded == ("HTTP_TRANSPORT_MODE",)
    assert os.environ["HTTP_TRANSPORT_MODE"] == "direct"
    assert os.environ["APP_DATA_DIR"] == "runtime-process"


def test_relative_runtime_paths_are_anchored_to_project_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_DATA_DIR", "runtime-from-env")
    monkeypatch.setenv("APP_DATABASE_PATH", "runtime-from-env/jobs.sqlite3")
    monkeypatch.setenv("SOURCE_REGISTRY_PATH", "data/custom-sources.json")

    settings = Settings.from_env()

    assert settings.data_dir == PROJECT_ROOT / "runtime-from-env"
    assert settings.database_path == PROJECT_ROOT / "runtime-from-env" / "jobs.sqlite3"
    assert settings.source_registry_path == PROJECT_ROOT / "data" / "custom-sources.json"
