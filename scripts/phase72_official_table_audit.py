"""Audit an official civil-service position table without writing to a database.

The report keeps table provenance and applies the same student publication gate
used by the application. It is intentionally an audit tool: it never creates
jobs, candidates, or publication records.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_hub.government_table_parser import load_position_table_with_report
from job_hub.government_table_parser import parse_position_table_rows
from job_hub.profiles import evaluate_student_publication


def build_report(
    path: Path,
    *,
    batch_id: str,
    source_id: str,
    notice_url: str,
    attachment_url: str,
    deadline_date: str,
) -> dict[str, object]:
    loaded = load_position_table_with_report(path)
    parsed = parse_position_table_rows(
        loaded["rows"],
        batch_id=batch_id,
        source_id=source_id,
        position_type="civil_service",
        province="",
        official_notice_url=notice_url,
        official_attachment_url=attachment_url,
        deadline_date=deadline_date,
        sheet_name="official",
    )
    counts: Counter[str] = Counter()
    public: list[dict[str, object]] = []
    for row in parsed["batch"]["rows"]:
        position_code, employer, title, major, degree, headcount, locator, location, province = row
        job = {
            "title": str(title),
            "employer": str(employer),
            "category": "公务员与选调",
            "source_id": source_id,
            "source_name": "国家公务员局2027年度考试录用公务员专题",
            "qualification_text": f"{major}；{degree}",
            "major_tags": str(major),
            "degree": str(degree),
            "location": str(location),
            "deadline_date": deadline_date,
            "status": "open",
            "country_or_region": "中国大陆",
            "official_evidence_url": attachment_url,
            "field_evidence": {
                "evidence_scope": "official_attachment_row",
                "岗位": str(title),
                "专业要求": str(major),
                "学历要求": str(degree),
                "工作地点": str(location),
                "岗位代码": str(position_code),
                "岗位行定位": str(locator),
            },
        }
        decision = evaluate_student_publication(
            job, include_extended_opportunities=True
        )
        counts[f"{decision.status}:{decision.label}"] += 1
        if decision.is_public:
            public.append(
                {
                    "position_code": str(position_code),
                    "employer": str(employer),
                    "title": str(title),
                    "major": str(major),
                    "degree": str(degree),
                    "location": str(location),
                    "headcount": headcount,
                    "locator": str(locator),
                    "publication_status": decision.status,
                    "publication_label": decision.label,
                    "major_match_mode": decision.major_match_mode,
                }
            )
    return {
        "report": "phase72_official_table_audit",
        "read_only": True,
        "table_path": str(path),
        "batch_id": batch_id,
        "source_id": source_id,
        "official_notice_url": notice_url,
        "official_attachment_url": attachment_url,
        "deadline_date": deadline_date,
        "sheets": loaded["sheets"],
        "skipped_sheets": loaded["skipped_sheets"],
        "input_rows": len(loaded["rows"]),
        "accepted_rows": len(parsed["batch"]["rows"]),
        "rejected_rows": len(parsed["rejected_rows"]),
        "decision_counts": dict(counts),
        "public_rows": len(public),
        "public_sample": public[:100],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-id", default="scs-guokao-2027")
    parser.add_argument("--source-id", default="scs-guokao-2027")
    parser.add_argument("--notice-url", default="http://bm.scs.gov.cn/kl2027")
    parser.add_argument(
        "--attachment-url",
        default="http://dl.scs.gov.cn/download/8a81f6d1a129c3d401a133dca26b00d2",
    )
    parser.add_argument("--deadline-date", default="2026-10-24")
    args = parser.parse_args()
    report = build_report(
        args.path,
        batch_id=args.batch_id,
        source_id=args.source_id,
        notice_url=args.notice_url,
        attachment_url=args.attachment_url,
        deadline_date=args.deadline_date,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("input_rows", "accepted_rows", "rejected_rows", "decision_counts", "public_rows")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
