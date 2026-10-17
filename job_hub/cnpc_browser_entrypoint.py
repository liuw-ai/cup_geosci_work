"""Install the optional Playwright client from a mounted wheel and run worker."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

from job_hub.browser_watchdog import start_watchdog
from job_hub.config import Settings, load_project_env


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
        allow_runtime_install = os.getenv(
            "CNPC_BROWSER_ALLOW_RUNTIME_INSTALL", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not allow_runtime_install:
            raise RuntimeError(
                "Playwright is unavailable in the browser worker image. "
                "Build Dockerfile.browser before starting this service; "
                "set CNPC_BROWSER_ALLOW_RUNTIME_INSTALL=true only for an "
                "explicit, controlled fallback installation."
            )
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


def wait_for_startup_window() -> None:
    """Stagger browser workers so a release does not fan out at once.

    The delay is operational only.  It does not alter robots, TLS or request
    headers; it prevents seven Chromium sessions from competing for the VPS
    and hitting every official portal in the same second after a restart.
    """
    raw = os.getenv("BROWSER_START_DELAY_SECONDS", "0").strip()
    try:
        delay = max(0, min(int(raw), 3_600))
    except ValueError:
        delay = 0
    if delay:
        time.sleep(delay)


def main() -> None:
    # Direct local launches need the same project-local configuration as
    # Docker Compose before Playwright's wheel/runtime paths are resolved.
    load_project_env()
    ensure_playwright()
    wait_for_startup_window()
    worker_kind = os.getenv("BROWSER_WORKER_KIND", "cnpc").strip().lower()
    configured_service_name = os.getenv("BROWSER_WORKER_SERVICE_NAME", "").strip()
    if configured_service_name:
        service_name = configured_service_name
    elif worker_kind == "cmgb":
        service_name = os.getenv("CMGB_BROWSER_SERVICE_NAME", "cmgb-browser")
    elif worker_kind == "cupb":
        service_name = os.getenv("CUPB_BROWSER_SERVICE_NAME", "cupb-browser")
    elif worker_kind == "cnooc":
        service_name = os.getenv("CNOOC_BROWSER_SERVICE_NAME", "cnooc-browser")
    elif worker_kind == "sinopec":
        service_name = os.getenv("SINOPEC_BROWSER_SERVICE_NAME", "sinopec-browser")
    elif worker_kind == "iguopin":
        service_name = os.getenv(
            "IGUOPIN_BROWSER_SERVICE_NAME", "chinalco-iguopin-browser"
        )
    elif worker_kind == "official":
        service_name = os.getenv("OFFICIAL_BROWSER_SERVICE_NAME", "pipechina-browser")
    else:
        service_name = os.getenv("CNPC_BROWSER_SERVICE_NAME", "cnpc-browser")
    start_watchdog(
        Settings.from_env(),
        service_name,
        max_age_seconds=max(
            300,
            int(os.getenv("BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "14400")),
        ),
    )
    if worker_kind == "cmgb":
        from job_hub.cmgb_browser_worker import main as worker_main
    elif worker_kind == "cupb":
        from job_hub.cupb_browser_worker import main as worker_main
    elif worker_kind == "iguopin":
        from job_hub.iguopin_browser_worker import main as worker_main
    elif worker_kind == "cnooc":
        from job_hub.cnooc_browser_worker import main as worker_main
    elif worker_kind == "sinopec":
        from job_hub.sinopec_browser_worker import main as worker_main
    elif worker_kind == "official":
        from job_hub.official_browser_worker import main as worker_main
    else:
        from job_hub.cnpc_browser_worker import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
