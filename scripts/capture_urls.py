"""Capture a supplied URL list as raw, hashable evidence files.

The command is deliberately source-neutral and does not touch the database.
It is used for official-source recovery after a third-party lead has supplied
an explicit URL.
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


def capture(url: str, path: Path) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "job-hub-phase4-evidence/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45, context=ssl.create_default_context()) as response:
            body = response.read()
            path.write_bytes(body)
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
            path.write_bytes(body)
        return {
            "status_code": error.code,
            "content_type": error.headers.get("Content-Type"),
            "bytes": len(body) or None,
            "sha256": hashlib.sha256(body).hexdigest() if body else None,
            "error": str(error),
        }
    except Exception as error:
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
    parser.add_argument("urls", nargs="+")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, url in enumerate(args.urls, start=1):
        path = args.output_dir / f"resource-{index:02d}.bin"
        result = capture(url, path)
        rows.append({
            "index": index,
            "url": url,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "path": path.as_posix() if path.exists() else None,
            **result,
        })
    report = {
        "phase": "phase4",
        "purpose": "read-only supplied URL evidence capture",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resources": rows,
    }
    (args.output_dir / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
