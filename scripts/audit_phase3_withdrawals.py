"""Read-only audit of historical withdrawal reasons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter
from typing import Any

from job_hub.db import Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    database = Database(args.database, read_only=True)
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT id, title, source_id, status,
                      COALESCE(withdrawal_reason, '') AS stored_reason,
                      COALESCE(withdrawal_evidence_url, '') AS evidence_url,
                      deadline_date, updated_at
               FROM jobs WHERE status = 'withdrawn'
               ORDER BY id"""
        ).fetchall()
        event_rows = connection.execute(
            """SELECT job_id, payload_json
               FROM job_events
               WHERE event_type = 'withdrawn'
               ORDER BY id"""
        ).fetchall()

    latest_event_reason: dict[int, str] = {}
    for row in event_rows:
        try:
            payload: Any = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            latest_event_reason[int(row["job_id"])] = str(payload.get("reason") or "").strip()

    def classify(stored: str, event: str) -> str:
        reason = (stored or event).strip().lower()
        if reason in {
            "major_mismatch",
            "deadline_passed",
            "official_cancelled",
            "official_closed",
            "official_replaced",
        }:
            return reason
        if not reason:
            return "unknown_manual_review"
        if any(token in reason for token in ("fresh", "stale", "30", "捕获", "新鲜", "未出现", "台账", "来源", "附件", "missing", "capture")):
            return "legacy_source_failure"
        if any(token in reason for token in ("专业不匹配", "专业不符", "major", "匹配")):
            return "major_mismatch_candidate"
        if any(token in reason for token in ("截止", "过期", "deadline", "expired")):
            return "deadline_passed_candidate"
        if any(token in reason for token in ("取消", "关闭", "替换", "cancel", "closed", "replaced")):
            return "official_closure_candidate"
        return "unknown_manual_review"

    audited = []
    counts: Counter[str] = Counter()
    for row in rows:
        item = dict(row)
        item["event_reason"] = latest_event_reason.get(int(row["id"]), "")
        item["classification"] = classify(
            str(row["stored_reason"] or ""), item["event_reason"]
        )
        counts[item["classification"]] += 1
        audited.append(item)
    print(
        json.dumps(
            {
                "database": str(args.database),
                "withdrawn_count": len(audited),
                "classification_counts": dict(counts),
                "requires_operator_review_count": sum(
                    count for label, count in counts.items()
                    if label.endswith("_manual_review") or label.endswith("_failure")
                    or label.endswith("_candidate")
                ),
                "requires_operator_review_job_ids": [
                    int(item["id"])
                    for item in audited
                    if item["classification"] in {
                        "unknown_manual_review",
                        "legacy_source_failure",
                        "major_mismatch_candidate",
                        "deadline_passed_candidate",
                        "official_closure_candidate",
                    }
                ],
                "review_samples": audited[:25],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
