from __future__ import annotations

from datetime import date

from job_hub.app import create_app

from conftest import make_settings


def _csrf(client):
    with client.session_transaction() as session:
        return session["admin_csrf_token"]


def _login(client):
    client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={
            "csrf_token": _csrf(client),
            "username": "admin",
            "password": "test-admin-password",
        },
        follow_redirects=False,
    )


def test_public_analytics_counts_pages_and_excludes_admin_and_bots(tmp_path):
    app = create_app(make_settings(tmp_path))
    public = app.test_client()
    public.get("/", headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    public.get("/jobs", headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    public.get("/", headers={"User-Agent": "Googlebot", "Accept": "text/html"})

    admin = app.test_client()
    assert _login(admin).status_code == 302
    admin.get("/")
    admin.get("/admin")

    summary = app.extensions["database"].analytics_summary(
        end_day=date.today().isoformat(), days=1
    )
    assert summary["totals"] == {"visits": 1, "unique_visitors": 1, "page_views": 2}
    assert app.extensions["database"].path.read_bytes()
    with app.extensions["database"].connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analytics_visitor_days").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM analytics_session_days").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM analytics_visitor_days WHERE visitor_key LIKE '%127.0.0.1%'").fetchone()[0] == 0


def test_analytics_is_private_and_admin_api_uses_machine_token(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/api/admin/analytics").status_code == 403
    response = client.get(
        "/api/admin/analytics",
        headers={"X-Admin-Token": settings.admin_token},
    )
    assert response.status_code == 200
    assert "trend" in response.get_json()
