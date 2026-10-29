"""Reindex a copied Phase 7.2 snapshot and report the resulting read path.

The input is opened through the normal database layer, but callers must pass a
copy. This command is intentionally separate from production worker commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.pipeline import JobPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_project_env()
    os.environ["APP_DATABASE_PATH"] = str(args.database.resolve())
    os.environ["APP_DATA_DIR"] = str(args.database.resolve().parent)
    settings = Settings.from_env()
    database = Database(args.database)
    before = database.count_open_jobs(as_of_date=args.as_of)
    result = JobPipeline(settings, database).reindex_jobs()
    after = database.count_open_jobs(as_of_date=args.as_of)
    jobs, _ = database.list_jobs(page_size=None, student_visible=False, only_open=False, as_of_date=args.as_of)
    status_counts: dict[str, int] = {}
    layer_counts: dict[str, int] = {}
    for job in jobs:
        status_counts[str(job.get("publication_status"))] = status_counts.get(str(job.get("publication_status")), 0) + 1
        layer_counts[str(job.get("publication_layer"))] = layer_counts.get(str(job.get("publication_layer")), 0) + 1
    report = {
        "report": "phase72_reindex_probe",
        "database": str(args.database),
        "as_of_date": args.as_of,
        "read_only_input": False,
        "reindex_result": result,
        "canonical_student_visible_before": before,
        "canonical_student_visible_after": after,
        "publication_status_counts": dict(sorted(status_counts.items())),
        "publication_layer_counts": dict(sorted(layer_counts.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "before": before,
        "after": after,
        "reclassified": result.get("reclassified"),
        "normalized": result.get("normalized"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
