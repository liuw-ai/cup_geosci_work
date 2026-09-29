"""Consistent, private SQLite backup and recovery primitives.

The application uses SQLite in WAL mode, so copying the database file with a
shell command while Web and Worker are alive is not a reliable backup.  This
module uses SQLite's online-backup API, validates every generated snapshot and
keeps all managed backups below ``APP_DATA_DIR``.  Restores remain an explicit
maintenance operation: callers must stop writers and pass ``confirm=True``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from job_hub.config import Settings


class BackupError(RuntimeError):
    """Raised when a backup is missing, invalid, or unsafe to operate on."""


@dataclass(frozen=True)
class BackupVerification:
    path: str
    exists: bool
    integrity: str
    required_tables_present: bool
    valid: bool
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupResult:
    path: str
    created_at: str
    bytes: int
    sha256: str
    verification: BackupVerification
    pruned_files: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["pruned_files"] = list(self.pruned_files)
        return result


@dataclass(frozen=True)
class BackupStatus:
    path: str | None
    age_seconds: int | None
    max_age_seconds: int
    verification: BackupVerification | None
    fresh: bool

    @property
    def ok(self) -> bool:
        return bool(self.verification and self.verification.valid and self.fresh)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["ok"] = self.ok
        return result


@dataclass(frozen=True)
class RestoreResult:
    restored_from: str
    restored_at: str
    emergency_backup_path: str | None
    emergency_backup_error: str | None
    verification: BackupVerification

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatabaseBackupManager:
    """Own managed backup files for one configured application database."""

    _PREFIX = "job_hub-"
    _SUFFIX = ".sqlite3"
    _REQUIRED_TABLES = frozenset({"sources", "jobs", "service_heartbeats"})

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database_path = settings.database_path.resolve()
        self.backup_dir = settings.managed_backup_dir().resolve()

    def backup_if_due(
        self,
        *,
        now: datetime | None = None,
    ) -> BackupResult | None:
        """Create a verified backup only when the configured interval elapsed."""
        current = now or datetime.now(timezone.utc)
        minimum_age = max(1, int(self.settings.backup_min_interval_minutes)) * 60
        status = self.latest_status(max_age_seconds=minimum_age, now=current)
        if status.verification and status.verification.valid and status.fresh:
            return None
        return self.create_backup(now=current)

    def create_backup(self, *, now: datetime | None = None) -> BackupResult:
        """Create a consistent SQLite snapshot and validate it before publishing it."""
        current = now or datetime.now(timezone.utc)
        if not self.database_path.is_file():
            raise BackupError(f"Database file does not exist: {self.database_path}")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        destination = self._next_backup_path(current)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            source = sqlite3.connect(self.database_path, timeout=30)
            target = sqlite3.connect(temporary)
            try:
                source.execute("PRAGMA busy_timeout = 30000")
                source.backup(target)
                target.commit()
            finally:
                target.close()
                source.close()
            self._fsync_file(temporary)
            verification = self.verify_path(temporary)
            if not verification.valid:
                raise BackupError(
                    f"Generated backup failed verification: {verification.detail or verification.integrity}"
                )
            os.replace(temporary, destination)
            self._fsync_directory(destination.parent)
            final_verification = self.verify_path(destination)
            if not final_verification.valid:
                raise BackupError(
                    f"Published backup failed verification: {final_verification.detail or final_verification.integrity}"
                )
            pruned = self._prune(now=current)
            return BackupResult(
                path=str(destination),
                created_at=current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                bytes=destination.stat().st_size,
                sha256=self._sha256(destination),
                verification=final_verification,
                pruned_files=tuple(str(path) for path in pruned),
            )
        except (OSError, sqlite3.Error) as error:
            raise BackupError(f"Database backup failed: {error}") from error
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except PermissionError:
                # The target connection is explicitly closed above. Retaining
                # a failed temporary file on Windows is safer than hiding the
                # original backup error; it is never selected as a managed
                # snapshot because it lacks the expected suffix.
                pass

    def verify_path(self, path: Path) -> BackupVerification:
        """Validate SQLite integrity and the minimal Job Hub application schema."""
        candidate = Path(path).resolve()
        if not candidate.is_file():
            return BackupVerification(
                path=str(candidate),
                exists=False,
                integrity="not_checked",
                required_tables_present=False,
                valid=False,
                detail="backup file does not exist",
            )
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"file:{candidate.as_posix()}?mode=ro", uri=True
            )
            try:
                row = connection.execute("PRAGMA integrity_check").fetchone()
                integrity = str(row[0]) if row else "missing_result"
                tables = {
                    str(item[0])
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as error:
            return BackupVerification(
                path=str(candidate),
                exists=True,
                integrity="unreadable",
                required_tables_present=False,
                valid=False,
                detail=str(error),
            )
        required_tables_present = self._REQUIRED_TABLES.issubset(tables)
        valid = integrity.lower() == "ok" and required_tables_present
        detail = None
        if integrity.lower() != "ok":
            detail = f"SQLite integrity_check returned {integrity}"
        elif not required_tables_present:
            detail = "backup does not contain the required Job Hub tables"
        return BackupVerification(
            path=str(candidate),
            exists=True,
            integrity=integrity,
            required_tables_present=required_tables_present,
            valid=valid,
            detail=detail,
        )

    def latest_status(
        self,
        *,
        max_age_seconds: int,
        now: datetime | None = None,
    ) -> BackupStatus:
        """Return the most recent managed backup and its freshness state."""
        maximum = max(1, int(max_age_seconds))
        current = now or datetime.now(timezone.utc)
        candidates = sorted(
            self._managed_backups(),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return BackupStatus(None, None, maximum, None, False)
        latest = candidates[0]
        age_seconds = max(
            0,
            int(current.timestamp() - latest.stat().st_mtime),
        )
        verification = self.verify_path(latest)
        return BackupStatus(
            path=str(latest),
            age_seconds=age_seconds,
            max_age_seconds=maximum,
            verification=verification,
            fresh=age_seconds <= maximum,
        )

    def restore_backup(self, backup_path: Path, *, confirm: bool = False) -> RestoreResult:
        """Restore a managed snapshot after callers have stopped all writers.

        ``confirm`` is intentionally mandatory.  The current database is backed
        up first when possible, so an accidental restoration has a recovery
        point too.  A damaged active database may not be backup-able; that error
        is recorded but does not prevent restoring the independently verified
        snapshot selected by the operator.
        """
        if not confirm:
            raise BackupError("Restore requires explicit confirmation")
        source = Path(backup_path).resolve()
        self._assert_managed_backup(source)
        verification = self.verify_path(source)
        if not verification.valid:
            raise BackupError(
                f"Refusing to restore an invalid backup: {verification.detail or verification.integrity}"
            )
        emergency_backup_path: str | None = None
        emergency_backup_error: str | None = None
        try:
            emergency_backup_path = self.create_backup().path
        except BackupError as error:
            emergency_backup_error = str(error)

        temporary = self.database_path.with_name(
            f".{self.database_path.name}.restore-{uuid.uuid4().hex}.tmp"
        )
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, temporary)
            self._fsync_file(temporary)
            temporary_verification = self.verify_path(temporary)
            if not temporary_verification.valid:
                raise BackupError(
                    "Staged restore file failed verification: "
                    f"{temporary_verification.detail or temporary_verification.integrity}"
                )
            os.replace(temporary, self.database_path)
            for suffix in ("-wal", "-shm"):
                Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)
            self._fsync_directory(self.database_path.parent)
        except (OSError, shutil.Error) as error:
            raise BackupError(f"Database restore failed: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        restored_verification = self.verify_path(self.database_path)
        if not restored_verification.valid:
            raise BackupError(
                "Database restore completed but validation failed: "
                f"{restored_verification.detail or restored_verification.integrity}"
            )
        return RestoreResult(
            restored_from=str(source),
            restored_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            emergency_backup_path=emergency_backup_path,
            emergency_backup_error=emergency_backup_error,
            verification=restored_verification,
        )

    def _next_backup_path(self, now: datetime) -> Path:
        timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = self.backup_dir / f"{self._PREFIX}{timestamp}{self._SUFFIX}"
        sequence = 1
        while candidate.exists():
            candidate = self.backup_dir / (
                f"{self._PREFIX}{timestamp}-{sequence}{self._SUFFIX}"
            )
            sequence += 1
        return candidate

    def _managed_backups(self) -> list[Path]:
        if not self.backup_dir.is_dir():
            return []
        return [
            path
            for path in self.backup_dir.iterdir()
            if path.is_file()
            and path.name.startswith(self._PREFIX)
            and path.name.endswith(self._SUFFIX)
        ]

    def _prune(self, *, now: datetime) -> list[Path]:
        retention_days = max(1, int(self.settings.backup_retention_days))
        cutoff = now.astimezone(timezone.utc).timestamp() - timedelta(
            days=retention_days
        ).total_seconds()
        removed: list[Path] = []
        for path in self._managed_backups():
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed.append(path)
        return removed

    def _assert_managed_backup(self, path: Path) -> None:
        try:
            path.relative_to(self.backup_dir)
        except ValueError as error:
            raise BackupError("Restore source must be inside BACKUP_STORAGE_DIR") from error

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb+") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
