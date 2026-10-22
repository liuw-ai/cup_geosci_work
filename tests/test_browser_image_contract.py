from pathlib import Path
from unittest.mock import patch

import pytest

from job_hub.cnpc_browser_entrypoint import ensure_playwright, wait_for_startup_window


ROOT = Path(__file__).resolve().parent.parent


def test_browser_worker_has_a_dedicated_image_with_build_time_playwright() -> None:
    app_dockerfile = (ROOT / "deploy" / "docker" / "Dockerfile.app").read_text(encoding="utf-8")
    dockerfile = (ROOT / "deploy" / "docker" / "Dockerfile.browser").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")

    assert "ARG RUNTIME_IMAGE=cupb-geoscience-job-hub:latest" in dockerfile
    assert "ARG BROWSER_DEPS_IMAGE=cupb-geoscience-job-hub-browser:bd61c09-general" in dockerfile
    assert "FROM ${RUNTIME_IMAGE}" in dockerfile
    # Official browser workers must verify the public CA chain before a
    # robots/access result is classified. The slim base image has no
    # guaranteed system trust store unless this package is explicit.
    assert "ca-certificates" in app_dockerfile
    assert "requirements-browser.txt" in dockerfile
    # The canonical browser image is built directly from Dockerfile.browser;
    # historical phase overlays are archived and must not be a test/runtime
    # dependency.
    assert "COPY --chown=jobhub:jobhub job_hub/browser_capture.py" in dockerfile
    assert "COPY --chown=jobhub:jobhub job_hub/official_browser_worker.py" in dockerfile
    assert "phase109-overlay" not in compose
    assert "overlay" not in compose.lower()
    assert "pip install --no-cache-dir" in dockerfile
    # The shared build anchor keeps the Dockerfile declaration in one place.
    assert compose.count("dockerfile: deploy/docker/Dockerfile.browser") == 1
    assert "BROWSER_DEPS_IMAGE: ${BROWSER_DEPS_IMAGE:-cupb-geoscience-job-hub-browser:bd61c09-general}" in compose
    assert "cnooc-browser:" in compose
    assert "BROWSER_WORKER_KIND: cnooc" in compose
    assert "pipechina-browser:" in compose
    assert "BROWSER_WORKER_KIND: official" in compose
    assert "chinalco-browser:" in compose
    assert "BROWSER_WORKER_KIND: iguopin" in compose
    assert "IGUOPIN_BROWSER_SOURCE_ID: chinalco-iguopin-browser" in compose
    assert "iguopin-general-browser:" in compose
    assert "IGUOPIN_BROWSER_SOURCE_ID: iguopin-general-geoscience-browser" in compose
    assert "IGUOPIN_BROWSER_CDP_URL: http://iguopin-general-headless-shell:9222" in compose
    assert compose.count("build: *browser-build") == 8
    # Eight browser services plus the release-environment default.
    assert compose.count("JOB_HUB_BROWSER_IMAGE:-cupb-geoscience-job-hub-browser:latest") == 9
    assert "cupb-browser:" in compose
    assert "BROWSER_WORKER_KIND: cupb" in compose
    assert "CUPB_BROWSER_CDP_URL: http://cupb-headless-shell:9222" in compose
    assert "COPY --chown=jobhub:jobhub job_hub/browser_watchdog.py" in dockerfile
    # chromedp/headless-shell's entrypoint starts Chrome on 9223 and exposes
    # its internal socat proxy on 9222. Passing another 9222 flag here makes
    # Chrome and socat race for the same port and breaks CDP readiness.
    assert "--remote-debugging-address=0.0.0.0" not in compose
    assert "--remote-debugging-port=9222" not in compose
    assert compose.count("CDP_URL: http://headless-shell:9222") == 1
    assert compose.count("CDP_URL: http://cmgb-headless-shell:9222") == 1
    assert compose.count("CDP_URL: http://pipechina-headless-shell:9222") == 1
    assert compose.count("CDP_URL: http://chinalco-headless-shell:9222") == 1
    assert compose.count("CDP_URL: http://iguopin-general-headless-shell:9222") == 1
    assert "external: true" in compose
    assert "name: cupb-geoscience-job-hub_default" in compose
    # Release data is deliberately parameterized so each immutable release
    # can bind its own isolated volume without changing this Compose contract.
    assert "name: ${JOB_HUB_DATA_VOLUME:-cupb-geoscience-job-hub_job_hub_data}" in compose
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


def test_browser_startup_delay_is_bounded_and_configurable(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setenv("BROWSER_START_DELAY_SECONDS", "125")
    with patch("job_hub.cnpc_browser_entrypoint.time.sleep", calls.append):
        wait_for_startup_window()
    assert calls == [125]

    monkeypatch.setenv("BROWSER_START_DELAY_SECONDS", "not-a-number")
    with patch("job_hub.cnpc_browser_entrypoint.time.sleep") as sleep:
        wait_for_startup_window()
    sleep.assert_not_called()
