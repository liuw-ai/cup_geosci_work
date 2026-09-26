"""Install the optional Playwright client from a mounted wheel and run worker."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def ensure_playwright() -> None:
    target = Path(
        os.getenv("CNPC_BROWSER_PYTHON_TARGET", "/var/lib/job-hub/python-packages")
    )
    target.mkdir(parents=True, exist_ok=True)
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    if importlib.util.find_spec("playwright") is not None:
        return
    wheel = Path(
        os.getenv(
            "CNPC_BROWSER_PLAYWRIGHT_WHEEL",
            "/opt/browser-wheels/playwright-1.48.0-py3-none-manylinux1_x86_64.whl",
        )
    )
    if wheel.is_file():
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(target),
            "--no-index",
            "--find-links",
            str(wheel.parent),
            "playwright==1.48.0",
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(target),
            "-r",
            "/app/requirements-browser.txt",
        ]
    subprocess.run(command, check=True)


def main() -> None:
    ensure_playwright()
    from job_hub.cnpc_browser_worker import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
