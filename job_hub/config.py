from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from job_hub.transport import validate_http_client, validate_transport_mode


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOTENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_env(path: Path | str | None = None) -> tuple[str, ...]:
    """Load simple project-local ``.env`` values without overriding real env.

    Docker Compose already injects its ``env_file``. Direct local commands do
    not, so they need the same configuration source for reproducible source
    transport and database paths. Process environment values always win over
    the file, which keeps test and deployment overrides explicit.
    """

    env_path = Path(path) if path is not None else PROJECT_ROOT / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise ValueError(f"cannot read project environment file: {env_path}") from error

    loaded: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _DOTENV_KEY.fullmatch(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return tuple(loaded)


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


def _env_optional_int(name: str, default: int | None) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    if value.strip().lower() in {"none", "null", "disabled", "off"}:
        return None
    return int(value)


@dataclass(frozen=True)
class Settings:
    site_name: str
    site_attribution: str
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
    artifact_storage_dir: Path | None = None
    government_artifact_manifest_path: Path | None = None
    government_position_registry_path: Path | None = None
    # A registry is a reviewed snapshot, not a perpetual source.  Once it is
    # older than this window, its rows are withdrawn until the official tables
    # are re-checked and the registry's ``as_of`` date is advanced.
    government_position_max_age_hours: int = 48
    # Official position tables can open between two expensive all-source
    # collection cycles. Keep their narrow evidence recheck cadence separate
    # so a guarded source can become publishable promptly without tripling
    # load on every career site.
    government_revalidation_interval_minutes: int = 30
    # Live official pages do not always publish a fixed deadline. Production
    # still needs a bounded read-side freshness contract so an outage cannot
    # leave an undated vacancy visible indefinitely. Tests may leave this
    # unset; Settings.from_env enables the production default.
    default_source_max_age_hours: int | None = None
    attachment_max_bytes: int = 25_000_000
    attachment_discovery_max_bytes: int = 2_000_000
    attachment_max_rows: int = 2_000
    attachment_max_text_characters: int = 250_000
    attachment_ocr_enabled: bool = False
    attachment_ocr_language: str = "chi_sim+eng"
    attachment_ocr_max_pages: int = 12
    # A bounded queue prevents one synchronization cycle from monopolizing
    # the worker while ensuring older registered artifacts are not starved by
    # newly discovered files.
    attachment_process_batch_limit: int = 500
    http_transport_mode: str = "environment"
    http_client: str = "requests"
    http_retry_attempts: int = 2
    http_backoff_seconds: float = 0.75
    http_max_backoff_seconds: float = 30.0
    http_jitter_seconds: float = 0.15
    http_cache_enabled: bool = True
    http_cache_max_entries: int = 2_000
    backup_storage_dir: Path | None = None
    backup_retention_days: int = 14
    backup_min_interval_minutes: int = 720
    # Failed sources are retried independently from successful sync cadence.
    # Policy/access failures get a longer cooldown so the worker does not
    # repeatedly hit a source that has explicitly limited automated access.
    source_retry_base_seconds: int = 300
    source_retry_max_seconds: int = 21_600
    source_blocked_retry_base_seconds: int = 21_600
    source_blocked_retry_max_seconds: int = 86_400
    # Retry failed cycles promptly instead of waiting for the full source
    # interval; the worker keeps a degraded heartbeat until recovery.
    worker_failure_retry_seconds: int = 60
    # Collection and lifecycle mutations must be explicitly enabled for each
    # reviewed release. A running worker in a local review or fresh deploy is
    # otherwise an observability-only paused process.
    worker_automation_enabled: bool = False
    # Official-identity expansion candidates are not registered collectors.
    # Check their public entries separately so a newly reachable portal is
    # recorded for adapter work without treating it as a job source.
    domestic_entry_health_interval_minutes: int = 360
    # Public discovery channels are a bounded lead-finding aid, not official
    # vacancy sources.  Keep their cadence independent from source sync so a
    # blocked aggregator cannot delay student-facing data refreshes.
    discovery_scan_interval_minutes: int = 360
    # Production readiness can require a fresh daily official-link report.
    # This remains opt-in so local development does not fail with an empty
    # runtime directory.
    link_health_enabled: bool = False
    link_health_sample_size: int = 20
    # Release identity is injected by the deployment pipeline.  Keeping it
    # in Settings makes the running Web/Worker/browser bundle observable
    # without coupling the application to Git or Docker at runtime.
    release_version: str = "dev"
    release_git_sha: str = "unknown"
    release_image_sha: str = "unknown"
    release_built_at: str = "unknown"

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
            site_attribution=os.getenv(
                "SITE_ATTRIBUTION",
                "中国石油大学（北京）地球科学学院就业信息项目",
            ).strip(),
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
            max_source_items=_env_int("MAX_SOURCE_ITEMS", 80),
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
            artifact_storage_dir=Path(
                os.getenv("ATTACHMENT_STORAGE_DIR", data_dir / "official-attachments")
            ),
            government_artifact_manifest_path=Path(
                os.getenv(
                    "GOVERNMENT_ARTIFACT_MANIFEST_PATH",
                    PROJECT_ROOT / "data" / "government_artifact_manifest.json",
                )
            ),
            government_position_registry_path=Path(
                os.getenv(
                    "GOVERNMENT_POSITION_REGISTRY_PATH",
                    PROJECT_ROOT / "data" / "government_position_registry.json",
                )
            ),
            government_position_max_age_hours=_env_int(
                "GOVERNMENT_POSITION_MAX_AGE_HOURS", 48
            ),
            government_revalidation_interval_minutes=max(
                1, _env_int("GOVERNMENT_REVALIDATION_INTERVAL_MINUTES", 30)
            ),
            default_source_max_age_hours=_env_optional_int(
                "DEFAULT_SOURCE_MAX_AGE_HOURS", 72
            ),
            attachment_max_bytes=_env_int("ATTACHMENT_MAX_BYTES", 25_000_000),
            attachment_discovery_max_bytes=_env_int(
                "ATTACHMENT_DISCOVERY_MAX_BYTES", 2_000_000
            ),
            attachment_max_rows=_env_int("ATTACHMENT_MAX_ROWS", 2_000),
            attachment_max_text_characters=_env_int(
                "ATTACHMENT_MAX_TEXT_CHARACTERS", 250_000
            ),
            attachment_ocr_enabled=_env_bool("ATTACHMENT_OCR_ENABLED"),
            attachment_ocr_language=os.getenv("ATTACHMENT_OCR_LANGUAGE", "chi_sim+eng"),
            attachment_ocr_max_pages=_env_int("ATTACHMENT_OCR_MAX_PAGES", 12),
            attachment_process_batch_limit=_env_int(
                "ATTACHMENT_PROCESS_BATCH_LIMIT", 500
            ),
            http_transport_mode=validate_transport_mode(
                os.getenv("HTTP_TRANSPORT_MODE", "environment")
            ),
            http_client=validate_http_client(
                os.getenv("HTTP_CLIENT", "requests")
            ),
            http_retry_attempts=_env_int("HTTP_RETRY_ATTEMPTS", 2),
            http_backoff_seconds=float(os.getenv("HTTP_BACKOFF_SECONDS", "0.75")),
            http_max_backoff_seconds=float(
                os.getenv("HTTP_MAX_BACKOFF_SECONDS", "30"
                )
            ),
            http_jitter_seconds=float(os.getenv("HTTP_JITTER_SECONDS", "0.15")),
            http_cache_enabled=_env_bool("HTTP_CACHE_ENABLED", True),
            http_cache_max_entries=_env_int("HTTP_CACHE_MAX_ENTRIES", 2_000),
            backup_storage_dir=Path(os.getenv("BACKUP_STORAGE_DIR", "backups")),
            backup_retention_days=_env_int("BACKUP_RETENTION_DAYS", 14),
            backup_min_interval_minutes=_env_int(
                "BACKUP_MIN_INTERVAL_MINUTES", 720
            ),
            source_retry_base_seconds=_env_int(
                "SOURCE_RETRY_BASE_SECONDS", 300
            ),
            source_retry_max_seconds=_env_int(
                "SOURCE_RETRY_MAX_SECONDS", 21_600
            ),
            source_blocked_retry_base_seconds=_env_int(
                "SOURCE_BLOCKED_RETRY_BASE_SECONDS", 21_600
            ),
            source_blocked_retry_max_seconds=_env_int(
                "SOURCE_BLOCKED_RETRY_MAX_SECONDS", 86_400
            ),
            worker_failure_retry_seconds=max(
                5, _env_int("WORKER_FAILURE_RETRY_SECONDS", 60)
            ),
            worker_automation_enabled=_env_bool("WORKER_AUTOMATION_ENABLED", False),
            domestic_entry_health_interval_minutes=max(
                30, _env_int("DOMESTIC_ENTRY_HEALTH_INTERVAL_MINUTES", 360)
            ),
            discovery_scan_interval_minutes=max(
                30, _env_int("DISCOVERY_SCAN_INTERVAL_MINUTES", 360)
            ),
            link_health_enabled=_env_bool("LINK_HEALTH_ENABLED", False),
            link_health_sample_size=max(
                1, min(_env_int("LINK_HEALTH_SAMPLE_SIZE", 20), 500)
            ),
            release_version=os.getenv("APP_RELEASE_VERSION", "dev").strip() or "dev",
            release_git_sha=os.getenv("APP_RELEASE_GIT_SHA", "unknown").strip()
            or "unknown",
            release_image_sha=os.getenv("APP_RELEASE_IMAGE_SHA", "unknown").strip()
            or "unknown",
            release_built_at=os.getenv("APP_RELEASE_BUILT_AT", "unknown").strip()
            or "unknown",
        )

    def release_info(self) -> dict[str, str]:
        """Return the immutable identity of the currently running bundle."""
        return {
            "version": self.release_version,
            "git_sha": self.release_git_sha,
            "image_sha": self.release_image_sha,
            "built_at": self.release_built_at,
        }

    def ensure_runtime_paths(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.managed_artifact_dir().mkdir(parents=True, exist_ok=True)
        self.managed_backup_dir().mkdir(parents=True, exist_ok=True)

    def managed_artifact_dir(self) -> Path:
        """Return the private attachment directory, always below APP_DATA_DIR."""
        data_root = self.data_dir.resolve()
        configured = self.artifact_storage_dir or Path("official-attachments")
        # Relative storage paths are intentionally relative to APP_DATA_DIR so
        # the same .env works in a local checkout and inside the Docker image.
        candidate = (
            configured if configured.is_absolute() else self.data_dir / configured
        ).resolve()
        try:
            candidate.relative_to(data_root)
        except ValueError as error:
            raise ValueError(
                "ATTACHMENT_STORAGE_DIR must be located inside APP_DATA_DIR"
            ) from error
        return candidate

    def managed_backup_dir(self) -> Path:
        """Return the private backup directory, always below ``APP_DATA_DIR``."""
        data_root = self.data_dir.resolve()
        configured = self.backup_storage_dir or Path("backups")
        candidate = (
            configured if configured.is_absolute() else self.data_dir / configured
        ).resolve()
        try:
            candidate.relative_to(data_root)
        except ValueError as error:
            raise ValueError(
                "BACKUP_STORAGE_DIR must be located inside APP_DATA_DIR"
            ) from error
        return candidate
