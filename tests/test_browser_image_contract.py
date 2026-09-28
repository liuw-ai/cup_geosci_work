from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_browser_worker_has_a_dedicated_image_with_build_time_playwright() -> None:
    dockerfile = (ROOT / "Dockerfile.browser").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.browser.yml").read_text(encoding="utf-8")

    assert "FROM cupb-geoscience-job-hub:latest" in dockerfile
    assert "requirements-browser.txt" in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert compose.count("dockerfile: Dockerfile.browser") == 2
    assert compose.count("image: cupb-geoscience-job-hub-browser:latest") == 2
    assert compose.count("--remote-debugging-address=0.0.0.0") == 2
    assert compose.count("--remote-debugging-port=9222") == 2
    assert "ports:" not in compose
    # The browser service must not fall back to installing packages in the
    # ordinary application container or depend on a writable wheel cache.
    assert "wheels-linux" not in compose
