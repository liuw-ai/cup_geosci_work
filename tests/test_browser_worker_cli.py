from __future__ import annotations

import subprocess
import sys


BROWSER_WORKER_MODULES = (
    "job_hub.cnooc_browser_worker",
    "job_hub.cmgb_browser_worker",
    "job_hub.cnpc_browser_worker",
    "job_hub.cupb_browser_worker",
    "job_hub.iguopin_browser_worker",
    "job_hub.official_browser_worker",
    "job_hub.sinopec_browser_worker",
)


def test_browser_workers_handle_help_without_starting_a_worker() -> None:
    for module in BROWSER_WORKER_MODULES:
        result = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        assert result.returncode == 0, (module, result.stdout, result.stderr)
        assert "usage:" in result.stdout.lower()


def test_browser_workers_reject_unknown_cli_arguments() -> None:
    for module in BROWSER_WORKER_MODULES:
        result = subprocess.run(
            [sys.executable, "-m", module, "--invalid-flag"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        assert result.returncode == 2, (module, result.stdout, result.stderr)
