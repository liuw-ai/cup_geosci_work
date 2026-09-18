from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    site_name: str
    secret_key: str
    base_url: str
    timezone: str
    data_dir: Path
    database_path: Path
    source_registry_path: Path
    daily_publish_time: str
    source_sync_interval_minutes: int
    max_source_items: int
    request_timeout_seconds: int
    admin_token: str
    mail_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: tuple[str, ...]
    smtp_use_ssl: bool
    crawl_run_stale_seconds: int = 1800

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("APP_DATA_DIR", PROJECT_ROOT / "runtime"))
        database_path = Path(
            os.getenv("APP_DATABASE_PATH", data_dir / "job_hub.sqlite3")
        )
        recipients = tuple(
            item.strip()
            for item in os.getenv("SMTP_TO", "").split(",")
            if item.strip()
        )
        return cls(
            site_name=os.getenv("SITE_NAME", "地学就业信息站"),
            secret_key=os.getenv("APP_SECRET_KEY", "development-only-change-me"),
            base_url=os.getenv("APP_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
            timezone=os.getenv("APP_TIMEZONE", "Asia/Shanghai"),
            data_dir=data_dir,
            database_path=database_path,
            source_registry_path=Path(
                os.getenv(
                    "SOURCE_REGISTRY_PATH",
                    PROJECT_ROOT / "data" / "sources.json",
                )
            ),
            daily_publish_time=os.getenv("DAILY_PUBLISH_TIME", "20:00"),
            source_sync_interval_minutes=_env_int(
                "SOURCE_SYNC_INTERVAL_MINUTES", 180
            ),
            max_source_items=_env_int("MAX_SOURCE_ITEMS", 40),
            request_timeout_seconds=_env_int("REQUEST_TIMEOUT_SECONDS", 20),
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            mail_enabled=_env_bool("MAIL_ENABLED"),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=_env_int("SMTP_PORT", 465),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_to=recipients,
            smtp_use_ssl=_env_bool("SMTP_USE_SSL", True),
            crawl_run_stale_seconds=_env_int("CRAWL_RUN_STALE_SECONDS", 1800),
        )

    def ensure_runtime_paths(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
