"""Run a bounded, read-only probe of official recovery targets.

The targets come from Phase 4 third-party discovery evidence.  A target is
fetched only after its robots policy is successfully read and permits the
same request user-agent.  The report records access health and content
metadata, but never downloads an attachment, parses a position table, or
writes the database.  A robots/TLS failure is recorded as unavailable rather
than treated as evidence that the underlying jobs do not exist.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "current" / "phase4-third-party-official-recovery-recheck-2026-10-19.json"
USER_AGENT = "job-hub-phase4-official-recovery/1.0"

TARGETS = (
    {
        "target_id": "shandong-geology-notice",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sydw.huatu.com/2026/0717/2868457.html",
        "official_url": "http://dkj.shandong.gov.cn/art/2026/7/17/art_356047_10333801.html",
        "kind": "official_notice",
    },
    {
        "target_id": "shandong-geology-table",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sydw.huatu.com/2026/0717/2868457.html",
        "official_url": "http://dkj.shandong.gov.cn/module/download/downfile.jsp?classid=0&filename=732c4f35c08742398d1c946003273bca.xlsx",
        "kind": "official_position_table",
    },
    {
        "target_id": "yunan-government-index",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sydw.huatu.com/2026/1016/2875246.html",
        "official_url": "https://www.gdyunan.gov.cn/ynxrmzf/rdzt/gsgg/index.html",
        "kind": "official_notice_index",
    },
    {
        "target_id": "yunan-hr-index",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sydw.huatu.com/2026/1016/2875246.html",
        "official_url": "http://www.gdyunan.gov.cn/yfynrsj/gkmlpt/index",
        "kind": "official_notice_index",
    },
    {
        "target_id": "shanxi-geology-notice",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sx.huatu.com/shehui/kaoshi/gg/2026/1017/1281187.html",
        "official_url": "https://www.sxddy.com/xinwendongtai/tongzhigonggao/2026-10-16/3507.html",
        "kind": "official_notice",
    },
    {
        "target_id": "nhri-notice",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sydw.huatu.com/2026/1016/2875231.html",
        "official_url": "https://www.nhri.cn/tzgg/zpxx/art/2026/art_01a4e42a69594ee88fac503a5cce8df5.html",
        "kind": "official_notice",
    },
    {
        "target_id": "nhri-position-table",
        "source_id": "huatu-discovery",
        "discovery_page_url": "https://sydw.huatu.com/2026/1016/2875231.html",
        "official_url": "https://www.nhri.cn/api-gateway/jpaas-web-server/front/document/download?fileUrl=YW5UzzlvCwcM%2FNHHX%2FtT6Du%2BYe4yVKZJwpy7%2FX41ZtH3jzrjBAGNZ7eb4VNI%2B6APA58hw%2FF1WFUBMght3OIgkp7GAzci%2F3n2KEszI2FJL6bAf3NVTU8toxcwhvxoQio5k%2BdtCRdf5Grdlw25haK7zN0%2BkqF4%2F7DxmTiQZ6sH7k%3D&fileName=%E9%99%84%E4%BB%B61%EF%BC%9A%E5%8D%97%E4%BA%AC%E6%B0%B4%E5%88%A9%E7%A7%91%E5%AD%A6%E7%A0%94%E7%A9%B6%E9%99%A2%E5%8F%AC%E5%BC%80%E6%8B%9B%E8%81%98%E4%BA%8B%E4%B8%9A%E7%BC%96%E5%88%B6%E5%B7%A5%E4%BD%9C%E4%BA%BA%E5%91%98%E5%B2%97%E4%BD%8D%E8%A1%A8.pdf",
        "kind": "official_position_table",
    },
)


def check_robots(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    result: dict[str, object] = {"robots_url": robots_url}
    try:
        response = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException as error:
        result.update(
            {
                "robots_status": None,
                "robots_allowed": False,
                "decision": "blocked_before_fetch",
                "reason": f"robots_unavailable:{error.__class__.__name__}",
            }
        )
        return result
    result["robots_status"] = response.status_code
    if response.status_code == 404:
        result.update({"robots_allowed": True, "decision": "permitted", "reason": "robots_not_found"})
        return result
    if not 200 <= response.status_code < 300:
        result.update(
            {
                "robots_allowed": False,
                "decision": "blocked_before_fetch",
                "reason": "robots_not_verifiable",
            }
        )
        return result
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    allowed = bool(parser.can_fetch(USER_AGENT, url))
    result.update(
        {
            "robots_allowed": allowed,
            "decision": "permitted" if allowed else "blocked_before_fetch",
            "reason": "permitted" if allowed else "disallowed",
        }
    )
    return result


def probe(target: dict[str, str]) -> dict[str, object]:
    target_url = target["official_url"]
    item: dict[str, object] = {
        **target,
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    robots = check_robots(target_url)
    item["robots"] = robots
    if not robots.get("robots_allowed"):
        item.update({"fetch_status": "not_attempted", "downloaded": False, "candidate_rows_created": 0})
        return item
    try:
        response = requests.get(target_url, headers={"User-Agent": USER_AGENT}, timeout=30, allow_redirects=True)
    except requests.RequestException as error:
        item.update(
            {
                "fetch_status": "source_unavailable",
                "fetch_error": f"{error.__class__.__name__}: {error}",
                "downloaded": False,
                "candidate_rows_created": 0,
            }
        )
        return item
    item.update(
        {
            "fetch_status": "fetched_metadata_only",
            "http_status": response.status_code,
            "final_url": response.url,
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "downloaded": False,
            "candidate_rows_created": 0,
            "note": "响应仅用于访问健康检查；未保存正文或附件。",
        }
    )
    return item


def main() -> int:
    rows = [probe(target) for target in TARGETS]
    payload = {
        "phase": "phase4",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "environment": "local_read_only",
        "policy": (
            "只在 robots 可验证且允许时请求官方 URL；不下载附件、不解析岗位、不写数据库。"
            "robots、TLS、网络或权限失败是来源暂时不可用证据，不表示岗位不存在。"
        ),
        "targets": rows,
        "summary": {
            "targets": len(rows),
            "robots_permitted": sum(bool(row["robots"].get("robots_allowed")) for row in rows),
            "fetch_attempted": sum(row.get("fetch_status") == "fetched_metadata_only" for row in rows),
            "downloaded": 0,
            "candidate_rows_created": 0,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
