"""Run the Phase 3 audit/backfill with an explicit dry-run/apply boundary."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from job_hub.phase3_audit import audit_and_backfill


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("runtime/job_hub.sqlite3"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--apply", action="store_true", help="persist the reviewed local migration")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_and_backfill(str(args.database), as_of=args.as_of, apply=args.apply)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
