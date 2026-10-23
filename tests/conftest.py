from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import pytest
from werkzeug.security import generate_password_hash

from job_hub.config import Settings


@pytest.fixture(scope="session", autouse=True)
def isolate_project_runtime(tmp_path_factory: pytest.TempPathFactory):
    """Keep every test-created default runtime outside the project checkout.

    A few CLI and integration paths intentionally construct ``Settings`` from
    environment variables.  The session guard makes those paths use an
    isolated SQLite file even when a test forgets to pass a temporary Settings
    object, so a full suite cannot mutate ``runtime/job_hub.sqlite3``.
    """

    sandbox = tmp_path_factory.mktemp("project-runtime")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("APP_DATA_DIR", str(sandbox))
    monkeypatch.setenv("APP_DATABASE_PATH", str(sandbox / "job_hub.sqlite3"))
    # Keep the default relative backup path relative to whichever APP_DATA_DIR
    # an individual CLI test selects.  A fixed absolute value here would leak
    # the session sandbox into tests that intentionally override APP_DATA_DIR.
    try:
        yield
    finally:
        monkeypatch.undo()


def make_settings(tmp_path: Path) -> Settings:
    registry = tmp_path / "sources.json"
    registry.write_text("[]", encoding="utf-8")
    return Settings(
        site_name="测试就业信息站",
        site_attribution="测试项目归属",
        secret_key="test-secret",
        base_url="http://testserver",
        timezone="Asia/Shanghai",
        data_dir=tmp_path,
        database_path=tmp_path / "jobs.sqlite3",
        source_registry_path=registry,
        daily_publish_time="20:00",
        source_sync_interval_minutes=180,
        max_source_items=10,
        request_timeout_seconds=5,
        admin_token="test-admin-token",
        admin_username="admin",
        admin_password_hash=generate_password_hash("test-admin-password"),
        admin_role="admin",
        mail_enabled=False,
        smtp_host="",
        smtp_port=465,
        smtp_username="",
        smtp_password="",
        smtp_from="",
        smtp_to=(),
        smtp_use_ssl=True,
    )


def make_settings_with_artifact_path(
    tmp_path: Path, artifact_storage_dir: Path
) -> Settings:
    """Build test settings with an explicit attachment storage path."""
    return replace(
        make_settings(tmp_path), artifact_storage_dir=artifact_storage_dir
    )


def source() -> dict[str, object]:
    return {
        "id": "official-test-source",
        "name": "测试官方招聘信息",
        "publisher": "测试能源集团",
        "homepage_url": "https://careers.example.edu.cn/",
        "source_type": "manual",
        "category": "三桶油与油服",
        "source_tier": "A",
        "enabled": True,
        "config": {"minimum_relevance": 0},
    }


def write_registry(path: Path, items: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
