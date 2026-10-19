from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPORT = Path("docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_hbdk_official_table_report_keeps_row_evidence_and_blocks_publication() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    capture = payload["capture"]
    raw = Path(capture["retained_path"])

    assert payload["source"]["official_notice_url"] == "https://www.hbdkjt.cn/xxgk_1/383.html"
    assert payload["source"]["official_table_url"] == "https://docs.qq.com/doc/DTFFuZmpRQ1FxR3JU"
    assert capture["http_status"] == 200
    assert raw.is_file()
    assert capture["sha256"] == _sha256(raw)
    assert len(payload["rows"]) == 2
    assert {row["title"] for row in payload["rows"]} == {"技术员", "部长副职"}
    assert all(row["student_layer_assessment"] == "broad_geoscience_match" for row in payload["rows"])
    assert payload["official_deadline"]["resolved_calendar_date"] == "2026-10-23"
    assert payload["official_deadline"]["policy"] == "asia_shanghai_weekdays_inclusive"
    assert payload["publication_decision"]["formal_jobs_created"] == 2
    assert payload["publication_decision"]["student_visible"] is True
