from __future__ import annotations

import os

from job_hub.config import load_project_env


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
