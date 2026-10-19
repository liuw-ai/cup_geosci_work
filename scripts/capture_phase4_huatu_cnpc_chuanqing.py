"""Capture and optionally register the Huatu CNPC Chuanqing discovery page.

The page is a Huatu repost, not a CNPC first-party notice.  This script keeps
the raw response and row-level evidence as private discovery candidates.  It
never invents an official URL and refuses to write the formal runtime DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.db import Database


ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_URL = "https://www.sydw8.com/2024/0929/512415.html"
SOURCE_ID = "huatu-discovery"
USER_AGENT = "job-hub-phase4-third-party-evidence/1.0"
DEFAULT_OUTPUT = ROOT / "docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json"
DEFAULT_RETAIN_DIR = ROOT / "runtime/third-party-evidence/huatu-cnpc-chuanqing-2024-09-29"


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved)


def _read(url: str, timeout: int = 30) -> tuple[bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed reviewed URL
        return response.read(), {
            "status": str(getattr(response, "status", response.getcode())),
            "content_type": str(response.headers.get("Content-Type") or ""),
        }


def check_robots(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    body, metadata = _read(robots_url)
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    allowed = bool(parser.can_fetch(USER_AGENT, url))
    return {
        "robots_url": robots_url,
        "robots_status": int(metadata["status"]),
        "robots_allowed": allowed,
        "robots_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "decision": "permitted" if allowed else "blocked_before_fetch",
    }


def _clean(value: str) -> str:
    return " ".join(str(value or "").split())


def parse_page(body: bytes) -> dict[str, object]:
    html = body.decode("gb18030", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [_clean(line) for line in text.splitlines() if _clean(line)]
    positions: list[dict[str, object]] = []
    heading = re.compile(r"^(\d+)[、.．]\s*(.+)$")
    for line in lines:
        match = heading.match(line)
        if not match:
            continue
        number = int(match.group(1))
        title = _clean(match.group(2))
        if number <= 12 and "专家" in title and not any(row["row_number"] == number for row in positions):
            positions.append({"row_number": number, "title": title})
    positions.sort(key=lambda row: int(row["row_number"]))
    if len(positions) != 12:
        raise ValueError(f"expected 12 position headings, found {len(positions)}")
    qualification = next((line for line in lines if "具有博士学位" in line and "年龄" in line), "")
    deadline = next((line for line in lines if "报名时间" in line and "招满" in line), "")
    email_match = re.search(r"[A-Za-z0-9._%+-]+@cnpc\.com\.cn", text)
    return {
        "page_title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "employer": "川庆钻探工程有限公司",
        "organization_context": "中国石油集团川庆钻探工程有限公司",
        "positions": positions,
        "qualification_text": qualification,
        "deadline_text": deadline,
        "application_email": email_match.group(0) if email_match else None,
        "application_method": "发送个人简历、学历学位证书等材料至招聘邮箱；页面未提供官方报名系统 URL。",
        "official_url_status": "not_found",
        "official_notice_url": None,
        "official_table_url": None,
        "official_application_url": None,
        "major_match_status": "not_yet_verified",
        "publication_status": "candidate_only",
    }


def build_payload(output: Path, retain_dir: Path) -> dict[str, object]:
    robots = check_robots(DISCOVERY_URL)
    if not robots["robots_allowed"]:
        raise RuntimeError("robots.txt does not permit this discovery page")
    body, response = _read(DISCOVERY_URL)
    digest = hashlib.sha256(body).hexdigest()
    retain_dir.mkdir(parents=True, exist_ok=True)
    raw_path = retain_dir / "discovery-page.html"
    raw_path.write_bytes(body)
    parsed = parse_page(body)
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload: dict[str, object] = {
        "phase": "phase4",
        "generated_at": captured_at,
        "source_id": SOURCE_ID,
        "source_name": "华图教育国企招聘网",
        "discovery_url": DISCOVERY_URL,
        "trust_grade": "B",
        "robots": robots,
        "capture": {
            "status": int(response["status"]),
            "content_type": response["content_type"],
            "bytes": len(body),
            "sha256": digest,
            "captured_at": captured_at,
            "retained_path": _display_path(raw_path),
            "encoding": "GB18030-compatible decode for parsing; raw bytes retained",
        },
        "discovery": parsed,
        "official_recovery": {
            "status": "pending",
            "reason": "华图页面提供 cnpc.com.cn 邮箱，但未提供可核验的中国石油官方公告、岗位表或报名系统 URL。",
            "next_action": "继续检索中国石油招聘平台或川庆钻探官方公告；找到官方证据后逐行核验，再决定是否升级。",
        },
        "candidates": [],
        "publication_gate": "official_notice_or_table_or_application_url_required",
    }
    candidates = []
    for row in parsed["positions"]:
        candidates.append(
            {
                "lead_provider": "华图教育",
                "lead_url": f"{DISCOVERY_URL}#position-{row['row_number']}",
                "title": row["title"],
                "employer_hint": parsed["employer"],
                "location_hint": "未明确（页面主体介绍含成都，但岗位地点未逐项给出）",
                "province_hint": "四川",
                "published_date": "2024-09-29",
                "official_url": None,
                "official_source_id": None,
                "official_table_url": None,
                "official_application_url": None,
                "third_party_table_url": None,
                "trust_grade": "B",
                "discovery_source_id": SOURCE_ID,
                "verification_note": "华图转载发现；尚无岗位级中国石油官方 URL，保持私有候选，不得进入学生端。",
                "metadata": {
                    "batch_id": "huatu-cnpc-chuanqing-high-level-2024-09-29",
                    "row_number": row["row_number"],
                    "locator": f"position-{row['row_number']}",
                    "discovery_url": DISCOVERY_URL,
                    "discovery_sha256": digest,
                    "discovery_capture_path": _display_path(raw_path),
                    "qualification_text": parsed["qualification_text"],
                    "deadline_text": parsed["deadline_text"],
                    "application_email": parsed["application_email"],
                    "application_method": parsed["application_method"],
                    "official_recovery_status": "pending",
                    "publication_status": "candidate_only",
                    "source_grade": "B",
                },
            }
        )
    payload["candidates"] = candidates
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def register_candidates(database_path: Path, payload: dict[str, object]) -> list[dict[str, object]]:
    formal = (ROOT / "runtime/job_hub.sqlite3").resolve()
    if database_path.resolve() == formal:
        raise ValueError("refusing to write the formal runtime database")
    database = Database(database_path)
    database.initialize()
    stored: list[dict[str, object]] = []
    for candidate in payload["candidates"]:
        row = database.create_candidate_lead(candidate)
        stored.append(row)
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retain-dir", type=Path, default=DEFAULT_RETAIN_DIR)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    payload = build_payload(args.output, args.retain_dir)
    result: dict[str, object] = {
        "report": str(args.output.relative_to(ROOT)).replace("\\", "/"),
        "raw_capture": payload["capture"],
        "candidate_count": len(payload["candidates"]),
        "formal_jobs_created": 0,
    }
    if args.database:
        stored = register_candidates(args.database, payload)
        result["candidate_ids"] = [int(row["id"]) for row in stored]
        result["database"] = str(args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
