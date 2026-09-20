from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from job_hub.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    registry = tmp_path / "sources.json"
    registry.write_text("[]", encoding="utf-8")
    return Settings(
        site_name="测试就业信息站",
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
