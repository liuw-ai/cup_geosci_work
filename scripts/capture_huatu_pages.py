"""Capture already identified Huatu lead pages as raw bytes.

The capture is an isolated evidence artifact.  It does not create or update
candidate leads and does not treat Huatu content as an application source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


URLS = (
    "https://sydw.huatu.com/2026/1020/2875586.html",
    "https://sydw.huatu.com/2026/1020/2875548.html",
    "http://www.huatu.com/2026/0803/2869813.html",
    "https://www.sydw8.com/2026/0811/540392.html",
    "https://www.sydw8.com/2026/0811/540390.html",
    "https://www.sydw8.com/2026/0811/540389.html",
    "https://www.sydw8.com/2026/0729/540211.html",
)


def capture(url: str, output: Path) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "job-hub-phase4-evidence/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45, context=ssl.create_default_context()) as response:
            body = response.read()
            output.write_bytes(body)
            return {
                "status_code": response.status,
                "content_type": response.headers.get("Content-Type"),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "error": None,
            }
    except urllib.error.HTTPError as error:
        body = error.read()
        if body:
            output.write_bytes(body)
        return {
            "status_code": error.code,
            "content_type": error.headers.get("Content-Type"),
            "bytes": len(body) or None,
            "sha256": hashlib.sha256(body).hexdigest() if body else None,
            "error": str(error),
        }
    except Exception as error:  # network/TLS failures are evidence, not retries forever
        return {
            "status_code": None,
            "content_type": None,
            "bytes": None,
            "sha256": None,
            "error": f"{type(error).__name__}: {error}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, url in enumerate(URLS, start=1):
        path = args.output_dir / f"page-{index:02d}.html"
        result = capture(url, path)
        rows.append(
            {
                "index": index,
                "url": url,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "html_path": path.as_posix() if path.exists() else None,
                **result,
            }
        )
    report = {
        "phase": "phase4",
        "purpose": "read-only raw Huatu lead page evidence capture",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pages": rows,
    }
    (args.output_dir / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
