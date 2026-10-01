from pathlib import Path
from unittest.mock import patch

import pytest

from job_hub.cnpc_browser_entrypoint import ensure_playwright


ROOT = Path(__file__).resolve().parent.parent


def test_browser_worker_has_a_dedicated_image_with_build_time_playwright() -> None:
    app_dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile.browser").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.browser.yml").read_text(encoding="utf-8")

    assert "FROM cupb-geoscience-job-hub:latest" in dockerfile
    # Official browser workers must verify the public CA chain before a
    # robots/access result is classified. The slim base image has no
    # guaranteed system trust store unless this package is explicit.
    assert "ca-certificates" in app_dockerfile
    assert "requirements-browser.txt" in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert compose.count("dockerfile: Dockerfile.browser") == 4
    assert "cnooc-browser:" in compose
    assert "BROWSER_WORKER_KIND: cnooc" in compose
    assert compose.count("image: cupb-geoscience-job-hub-browser:latest") == 4
    # chromedp/headless-shell's entrypoint starts Chrome on 9223 and exposes
    # its internal socat proxy on 9222. Passing another 9222 flag here makes
    # Chrome and socat race for the same port and breaks CDP readiness.
    assert "--remote-debugging-address=0.0.0.0" not in compose
    assert "--remote-debugging-port=9222" not in compose
    assert compose.count("CDP_URL: http://headless-shell:9222") == 1
    assert compose.count("CDP_URL: http://cmgb-headless-shell:9222") == 1
    assert "ports:" not in compose
    # The browser service must not fall back to installing packages in the
    # ordinary application container or depend on a writable wheel cache.
    assert "wheels-linux" not in compose


def test_browser_entrypoint_fails_fast_without_runtime_install_permission(tmp_path) -> None:
    with patch.dict("os.environ", {
        "CNPC_BROWSER_PYTHON_TARGET": str(tmp_path / "packages"),
        "CNPC_BROWSER_PLAYWRIGHT_WHEEL": str(tmp_path / "missing.whl"),
        "CNPC_BROWSER_ALLOW_RUNTIME_INSTALL": "false",
    }, clear=False), patch("job_hub.cnpc_browser_entrypoint.importlib.util.find_spec", return_value=None):
        with pytest.raises(RuntimeError, match="Build Dockerfile.browser"):
            ensure_playwright()
