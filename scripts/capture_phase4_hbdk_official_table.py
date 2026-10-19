"""Capture the official HBDK position table referenced by a Huatu discovery page.

The page is an official Tencent Docs table linked from the employer's official
notice.  This script keeps the raw response in ``runtime/`` and writes a
small, hash-bound report.  It deliberately does not create a job or bypass
robots, login, CAPTCHA, or browser permissions.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from job_hub.phase3_policy import resolve_working_day_deadline


ROOT = Path(__file__).resolve().parent.parent
OFFICIAL_TABLE_URL = "https://docs.qq.com/doc/DTFFuZmpRQ1FxR3JU"
OFFICIAL_NOTICE_URL = "https://www.hbdkjt.cn/xxgk_1/383.html"
DISCOVERY_URL = "https://he.huatu.com/2026/1015/1714501.html"
CAPTURE_DIR = ROOT / "runtime" / "third-party-evidence" / "hbdk-hebei-2026-10-19"
CAPTURE_PATH = CAPTURE_DIR / "official-position-table.html"
REPORT_PATH = ROOT / "docs" / "current" / "phase4-third-party-hbdk-official-table-2026-10-19.json"


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\\s+", " ", data).strip()
        if value:
            self.parts.append(value)


def fetch() -> tuple[bytes, str, int]:
    request = Request(
        OFFICIAL_TABLE_URL,
        headers={"User-Agent": "job-hub-phase4-official-evidence/1.0"},
    )
    with urlopen(request, timeout=30) as response:  # nosec B310: fixed official URL
        status = int(getattr(response, "status", response.getcode()))
        content_type = str(response.headers.get("Content-Type") or "")
        body = response.read(2_000_000)
    return body, content_type, status


def main() -> int:
    body, content_type, status = fetch()
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURE_PATH.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()

    parser = _TextParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    text = "\\n".join(parser.parts)
    title = "河北省地球物理勘查有限公司2026年度第三次公开招聘岗位信息表"
    required_markers = [title, "技术员", "部长副职", "矿产地质", "本科及以上"]
    if not all(marker in text for marker in required_markers):
        raise RuntimeError("official table response did not contain the reviewed row markers")

    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report = {
        "phase": "phase4",
        "captured_at": observed_at,
        "source": {
            "discovery_source_id": "huatu-discovery",
            "trust_grade": "B",
            "discovery_page_url": DISCOVERY_URL,
            "official_notice_url": OFFICIAL_NOTICE_URL,
            "official_table_url": OFFICIAL_TABLE_URL,
        },
        "capture": {
            "capture_mode": "official_web_table_response",
            "http_status": status,
            "content_type": content_type,
            "bytes": len(body),
            "sha256": digest,
            "retained_path": str(CAPTURE_PATH.relative_to(ROOT)).replace("\\", "/"),
        },
        "rows": [
            {
                "row_number": 1,
                "department": "物化探（水土修复）部",
                "title": "技术员",
                "position_category": "技术岗",
                "headcount": 1,
                "major_requirement": "矿产地质/地质工程勘查相关专业",
                "degree_requirement": "本科及以上",
                "location": "河北省廊坊市经济技术开发区银杏路26号",
                "student_layer_assessment": "broad_geoscience_match",
            },
            {
                "row_number": 2,
                "department": "物化探（水土修复）部",
                "title": "部长副职",
                "position_category": "管理岗",
                "headcount": 1,
                "major_requirement": "矿产地质/地质工程勘查相关专业",
                "degree_requirement": "本科及以上",
                "location": "河北省廊坊市经济技术开发区银杏路26号",
                "student_layer_assessment": "broad_geoscience_match",
            },
        ],
        "official_deadline": {
            "published_date": "2026-10-15",
            "text": "自发布之日起7个工作日内",
            "resolved_calendar_date": resolve_working_day_deadline("2026-10-15", 7),
            "policy": "asia_shanghai_weekdays_inclusive",
            "working_days": 7,
            "calendar_exceptions": [],
            "calendar_note": "以北京时间自然日计算，发布日期计为第1个工作日；2026-10-15至2026-10-23未使用额外节假日或调休例外。",
        },
        "publication_decision": {
            "status": "official_content_verified_ready_for_local_review",
            "candidate_leads_created": 2,
            "formal_jobs_created": 0,
            "student_visible": False,
            "reason": "官方岗位表逐行可读，截止日已按明确的北京时间工作日规则解析；正式岗位发布仍需本地正式库前的隔离验证。",
        },
        "policy_checks": {
            "third_party_discovery_is_not_application": True,
            "official_table_is_student_link_candidate": True,
            "robots_or_login_bypass": False,
            "server_deployment": False,
        },
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "sha256": digest, "rows": 2}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
