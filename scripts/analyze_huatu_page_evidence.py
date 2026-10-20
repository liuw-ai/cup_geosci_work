"""Extract row-level source and attachment evidence from captured Huatu pages.

This tool is intentionally read-only with respect to the formal database.  It
parses already captured bytes, records their hashes, and classifies links for
manual official-source recovery.  A Huatu URL is never promoted to an
application URL by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup


URL_RE = re.compile(r"https?://[^\s<>\"'，。；）)]+")
SOURCE_RE = re.compile(
    r"(?:原文来源|来源|文章来源)[:：]?\s*(https?://[^\s<>\"'，。；）)]+)"
)
ATTACHMENT_RE = re.compile(r"\.(?:xls|xlsx|csv|pdf|docx?|zip)(?:$|\?)", re.I)
CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_-]+)", re.I)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.rstrip(".,;") for value in values))


def _official_like(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return bool(
        re.search(
            r"(?:gov\.cn|edu\.cn|org\.cn|\.gov\.|\.edu\.|\.org\.)$", host
        )
        or host.endswith(("gov.cn", "edu.cn", "org.cn"))
    )


def analyze_page(path: Path) -> dict:
    raw = path.read_bytes()
    charset_match = CHARSET_RE.search(raw[:8192])
    charset = charset_match.group(1).decode("ascii", "ignore") if charset_match else "utf-8"
    try:
        html = raw.decode(charset, "replace")
    except LookupError:
        charset = "utf-8"
        html = raw.decode(charset, "replace")
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one(".artBcon") or soup
    heading = soup.select_one("h1")
    title = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
    text = " ".join(article.get_text(" ", strip=True).split())
    links: list[dict[str, str]] = []
    for anchor in article.select("a[href]"):
        url = urllib.parse.urljoin("https://www.huatu.com/", anchor["href"])
        label = " ".join(anchor.get_text(" ", strip=True).split())
        links.append({"label": label, "url": url})
    urls = _unique(URL_RE.findall(text))
    source_urls = _unique([match for match in SOURCE_RE.findall(text)])
    official_like_urls = _unique(
        [url for url in urls if _official_like(url)] + source_urls
    )
    application_urls = [
        url
        for url in urls
        if re.search(r"报名|zhaopin|kaowu|hxrc|招聘", url, re.I)
    ]
    attachments = [
        link for link in links if ATTACHMENT_RE.search(link["url"])
    ]
    return {
        "file": path.name,
        "title": title,
        "article_bytes": len(raw),
        "article_text_sha256": hashlib.sha256(raw).hexdigest(),
        "declared_charset": charset,
        "source_urls": source_urls,
        "official_like_urls": official_like_urls,
        "application_or_recruitment_urls": _unique(application_urls),
        "article_links": links,
        "attachment_links": attachments,
        "official_evidence_status": "needs_row_level_recovery",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [analyze_page(path) for path in sorted(args.capture_dir.glob("page-*.html"))]
    report = {
        "phase": "phase4",
        "source_id": "huatu-discovery",
        "purpose": "row-level evidence extraction from captured Huatu pages",
        "captured_dir": args.capture_dir.as_posix(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
    }
    output = args.output or args.capture_dir / "page-analysis.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "count": len(rows),
        "rows": [
            {
                "file": row["file"],
                "title": row["title"],
                "source_urls": row["source_urls"],
                "official_like_urls": row["official_like_urls"],
                "attachments": len(row["attachment_links"]),
                "application_urls": row["application_or_recruitment_urls"],
            }
            for row in rows
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
