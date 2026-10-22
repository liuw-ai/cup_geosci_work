"""Import a B-level third-party position table into the private lead queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.db import Database
from job_hub.third_party_tables import (
    fetch_and_normalize_third_party_table,
    ingest_third_party_table_file,
    persist_third_party_table_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--table-url", required=True)
    parser.add_argument(
        "--table-file",
        type=Path,
        help="operator-provided real table file; avoids network access but keeps --table-url as provenance",
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--position-type", default="civil_service")
    parser.add_argument("--province", required=True)
    parser.add_argument("--official-notice-url", default="")
    parser.add_argument("--official-table-url", default="")
    parser.add_argument("--official-application-url", default="")
    parser.add_argument("--discovery-page-url", default="")
    parser.add_argument("--deadline-date", default="")
    parser.add_argument("--location", default="")
    parser.add_argument("--database", type=Path, help="isolated SQLite path used only with --apply")
    parser.add_argument("--retain-dir", type=Path)
    parser.add_argument(
        "--allow-formal-database",
        action="store_true",
        help="explicitly permit writing runtime/job_hub.sqlite3; reserved for an approved release step",
    )
    parser.add_argument("--apply", action="store_true", help="persist candidates into candidate_leads")
    args = parser.parse_args()

    common = {
        "source_id": args.source_id,
        "third_party_table_url": args.table_url,
        "batch_id": args.batch_id,
        "position_type": args.position_type,
        "province": args.province,
        "official_notice_url": args.official_notice_url,
        "official_table_url": args.official_table_url,
        "official_application_url": args.official_application_url,
        "discovery_page_url": args.discovery_page_url,
        "deadline_date": args.deadline_date,
        "location": args.location,
        "storage_dir": args.retain_dir,
    }
    if args.table_file is not None:
        payload = ingest_third_party_table_file(table_path=args.table_file, **common)
    else:
        payload = fetch_and_normalize_third_party_table(**common)
    inserted = 0
    if args.apply:
        if args.database is None:
            parser.error("--apply requires an explicit isolated --database path")
        if args.retain_dir is None:
            parser.error("--apply requires --retain-dir so the downloaded evidence is retained")
        resolved_db = args.database.resolve()
        formal_db = (Path("runtime") / "job_hub.sqlite3").resolve()
        if resolved_db == formal_db and not args.allow_formal_database:
            parser.error(
                "refusing to write the formal runtime database; use an isolated copy, "
                "or obtain an approved release step before passing --allow-formal-database"
            )
        database = Database(resolved_db)
        database.initialize()
        persisted = persist_third_party_table_candidates(database, payload)
        inserted = int(persisted["linked_candidate_count"])
        payload["batch_ledger"] = {
            "batch_id": persisted["batch"]["batch_id"],
            "status": persisted["batch"]["status"],
            "linked_candidate_count": inserted,
        }
    payload["persisted_candidate_count"] = inserted
    payload["publication_gate"] = "official_content_verified_required"
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
