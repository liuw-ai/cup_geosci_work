from __future__ import annotations

from job_hub.db import Database, SQLITE_BUSY_TIMEOUT_MS


def test_database_connections_wait_for_shared_worker_startup_locks(tmp_path) -> None:
    database = Database(tmp_path / "job_hub.sqlite3")
    connection = database.connect()
    try:
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert timeout == SQLITE_BUSY_TIMEOUT_MS
    assert timeout >= 30_000
