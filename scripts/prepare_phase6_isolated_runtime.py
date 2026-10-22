"""Create a read-only-source, isolated Phase 6 SQLite review workspace.

The formal local runtime database is never opened for writing.  SQLite's
online-backup API produces a consistent copy under ``runtime/phase6``; schema
migrations and all candidate-table trials operate only on that copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.db import Database


DEFAULT_SOURCE = ROOT / "runtime" / "job_hub.sqlite3"
DEFAULT_WORKSPACE = ROOT / "runtime" / "phase6" / "review-2026-10-23"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "missing_result"
    finally:
        connection.close()


def create_isolated_runtime(
    *,
    source_database: Path = DEFAULT_SOURCE,
    workspace: Path = DEFAULT_WORKSPACE,
    phase6_root: Path | None = None,
) -> dict[str, Any]:
    """Copy a formal local database to an empty Phase 6 review workspace."""

    source = Path(source_database).resolve()
    target_root = Path(workspace).resolve()
    runtime_phase6_root = (phase6_root or (ROOT / "runtime" / "phase6")).resolve()
    if not source.is_file():
        raise ValueError(f"source database does not exist: {source}")
    try:
        target_root.relative_to(runtime_phase6_root)
    except ValueError as error:
        raise ValueError("Phase 6 workspace must be located under runtime/phase6") from error
    if source == target_root / "job_hub.sqlite3":
        raise ValueError("isolated target cannot be the source database")
    if target_root.exists() and any(target_root.iterdir()):
        raise ValueError(
            f"Phase 6 workspace is not empty: {target_root}; use a new workspace name"
        )
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / "job_hub.sqlite3"
    temporary = target_root / ".job_hub.sqlite3.copying"
    source_hash_before = sha256(source)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    temporary.replace(target)

    # Migration is intentionally applied after copying and therefore affects
    # only the review database. This makes Phase 6 tables available even when
    # the formal local runtime still reflects the Phase 5 schema.
    database = Database(target)
    database.initialize()
    source_hash_after = sha256(source)
    if source_hash_before != source_hash_after:
        raise RuntimeError("source database changed while creating the isolated copy")
    with database.connect() as connection:
        tables = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        )
        counts = {
            "jobs": int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            "candidate_leads": int(
                connection.execute("SELECT COUNT(*) FROM candidate_leads").fetchone()[0]
            ),
            "third_party_batches": int(
                connection.execute("SELECT COUNT(*) FROM third_party_batches").fetchone()[0]
            ),
        }
    manifest = {
        "phase": "phase6",
        "purpose": "isolated_local_review_workspace",
        "created_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source_database": str(source),
        "source_sha256": source_hash_before,
        "source_was_unchanged": True,
        "review_database": str(target),
        "review_database_sha256": sha256(target),
        "review_database_integrity": _sqlite_integrity(target),
        "schema_tables": tables,
        "counts_before_phase6_trial": counts,
        "write_boundary": (
            "All Phase 6 imports, review decisions, and verification exercises must use "
            "this review database. The source runtime database is not a Phase 6 write target."
        ),
    }
    (target_root / "isolation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    print(json.dumps(create_isolated_runtime(
        source_database=args.source_database,
        workspace=args.workspace,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
