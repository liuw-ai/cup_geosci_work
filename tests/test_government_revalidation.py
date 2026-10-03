from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from job_hub.db import Database
from job_hub.government_positions import current_publishable_position_records
from job_hub.government_revalidation import revalidate_government_sources
from job_hub.worker import DailyWorker

from conftest import make_settings, source


class FakeResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content
        self.closed = False
        self.yielded_bytes = 0

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.content), chunk_size):
            chunk = self.content[offset : offset + chunk_size]
            self.yielded_bytes += len(chunk)
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.trust_env = False
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        if url.endswith("/robots.txt") and url not in self.responses:
            return FakeResponse(404, b"")
        return self.responses[url]


class NoRequestSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.trust_env = False
        self.calls = 0

    def get(self, _url: str, **_kwargs: object) -> FakeResponse:
        self.calls += 1
        raise AssertionError("manual-only evidence recheck must not make HTTP requests")


def _registry() -> dict[str, object]:
    return {
        "as_of": "2026-09-20",
        "records": [
            {
                "source_id": "official-test-source",
                "record_status": "verified_open",
                "match_status": "explicit_match",
                "deadline_date": "2026-12-31",
                "official_notice_url": "https://careers.example.edu.cn/notice/1",
                "official_attachment_url": "https://careers.example.edu.cn/notice/1.xlsx",
            }
        ],
    }


def test_recheck_reads_only_registered_official_notice_and_attachment(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(206, b"PK\x03\x04xlsx"),
        }
    )

    results = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        now=datetime(2026, 9, 28, 2, tzinfo=timezone.utc),
        session=session,
    )

    assert len(results) == 1
    assert results[0]["status"] == "verified"
    assert results[0]["checked_at"] == "2026-09-28T02:00:00Z"
    assert "2 official evidence" in results[0]["detail"]
    assert session.calls == [
        "https://careers.example.edu.cn/robots.txt",
        "https://careers.example.edu.cn/notice/1",
        "https://careers.example.edu.cn/notice/1.xlsx",
    ]


def test_recheck_stops_before_evidence_when_robots_disallows_access(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/robots.txt": FakeResponse(
                200,
                b"User-agent: *\nDisallow: /notice/\n",
            ),
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"must not fetch"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(200, b"must not fetch"),
        }
    )

    result = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "source_unavailable"
    assert "robots.txt does not permit" in result["detail"]
    assert session.calls == ["https://careers.example.edu.cn/robots.txt"]


def test_recheck_rejects_html_error_document_at_robots_url(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/robots.txt": FakeResponse(
                200,
                b"<html><title>404 Not Found</title></html>",
            ),
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"must not fetch"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(200, b"must not fetch"),
        }
    )

    result = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "source_unavailable"
    assert "HTML error document" in result["detail"]
    assert session.calls == ["https://careers.example.edu.cn/robots.txt"]


def test_recheck_distinguishes_explicit_cancellation_from_source_failure(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, "本公告终止招聘".encode()),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(404, b"missing"),
        }
    )

    result = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "withdrawn"
    assert "cancellation" in result["detail"]


def test_manual_only_recheck_performs_zero_http_requests(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "manual_only",
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = NoRequestSession()

    result = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "manual_confirmation_required"
    assert session.calls == 0


def test_manual_only_source_never_uses_registry_fallback_as_publication_evidence() -> None:
    manual_sources = {"official-test-source"}
    assert current_publishable_position_records(
        _registry(),
        today="2026-09-28",
        max_age_hours=None,
        manual_confirmation_source_ids=manual_sources,
    ) == []

    confirmed = current_publishable_position_records(
        _registry(),
        today="2026-09-28",
        max_age_hours=48,
        manual_confirmation_source_ids=manual_sources,
        source_verifications={
            "official-test-source": {
                "status": "verified",
                "last_success_at": "2026-09-28T00:00:00Z",
            }
        },
        now=datetime(2026, 9, 28, 1, tzinfo=timezone.utc),
    )
    assert len(confirmed) == 1


def test_worker_preserves_prior_manual_confirmation(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "government_positions.json"
    registry_path.write_text(
        '{"version": 1, "as_of": "2026-09-28", "records": []}', encoding="utf-8"
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    worker.database.upsert_source(source())
    worker.database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at="2026-09-28T00:00:00Z",
        detail="Administrator manual confirmation: checked official evidence",
    )
    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [
            {
                "source_id": "official-test-source",
                "status": "manual_confirmation_required",
                "checked_at": "2026-09-28T01:00:00Z",
                "detail": "no automated request was made",
                "evidence_fingerprint": "",
            }
        ],
    )

    result = worker._revalidate_government_position_sources()
    verification = worker.database.list_government_source_verifications()[0]

    assert result["manual_confirmation_required"] == 1
    assert verification["status"] == "verified"
    assert verification["checked_at"] == "2026-09-28T00:00:00Z"


def test_source_recheck_failure_preserves_last_success_but_eventually_expires(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at="2026-09-28T00:00:00Z",
    )
    database.record_government_source_verification(
        "official-test-source",
        status="source_unavailable",
        checked_at="2026-09-28T01:00:00Z",
        detail="ConnectionError",
    )
    verification = database.list_government_source_verifications()[0]
    assert verification["last_success_at"] == "2026-09-28T00:00:00Z"
    events = database.list_government_source_verification_events("official-test-source")
    assert [event["status"] for event in events] == [
        "source_unavailable",
        "verified",
    ]
    assert database.government_source_refresh_counts() == {
        "official-test-source": {"total_refreshes": 2, "successful_refreshes": 1}
    }

    fresh = current_publishable_position_records(
        _registry(),
        today="2026-09-28",
        max_age_hours=48,
        source_verifications={"official-test-source": verification},
        now=datetime(2026, 9, 28, 2, tzinfo=timezone.utc),
    )
    assert len(fresh) == 1

    expired = current_publishable_position_records(
        _registry(),
        today="2026-10-01",
        max_age_hours=48,
        source_verifications={"official-test-source": verification},
        now=datetime(2026, 10, 1, 2, tzinfo=timezone.utc),
    )
    assert expired == []


def test_explicit_official_withdrawal_immediately_blocks_publication() -> None:
    records = current_publishable_position_records(
        _registry(),
        today="2026-09-28",
        max_age_hours=48,
        source_verifications={
            "official-test-source": {
                "status": "withdrawn",
                "last_success_at": "2026-09-28T00:00:00Z",
            }
        },
        now=datetime(2026, 9, 28, 1, tzinfo=timezone.utc),
    )

    assert records == []


def test_unconfigured_source_immediately_blocks_publication() -> None:
    records = current_publishable_position_records(
        _registry(),
        today="2026-09-28",
        max_age_hours=48,
        source_verifications={
            "official-test-source": {
                "status": "not_configured",
                "last_success_at": "2026-09-28T00:00:00Z",
            }
        },
        now=datetime(2026, 9, 28, 1, tzinfo=timezone.utc),
    )

    assert records == []


def test_recheck_stops_after_the_configured_range_limit(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(200, b"A" * 20_000),
        }
    )

    result = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "verified"
    attachment = session.responses["https://careers.example.edu.cn/notice/1.xlsx"]
    assert attachment.closed is True
    assert attachment.yielded_bytes == 8_192


def test_conditional_position_cancellation_text_does_not_withdraw_the_notice(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(
                200,
                "若岗位取消，由招聘单位通知已通过资格审查人员改报其他岗位。".encode(),
            ),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(200, b"PK\x03\x04xlsx"),
        }
    )

    result = revalidate_government_sources(
        _registry(),
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "verified"


def test_automatic_evidence_refresh_cannot_renew_current_vacancy_confirmation(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(source())

    database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at="2026-10-03T00:00:00Z",
        detail="Administrator checked the official current vacancy status.",
        availability_confirmed_at="2026-10-03T00:00:00Z",
        availability_confirmation_detail="Official status page still accepts applications.",
    )
    refreshed = database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at="2026-10-04T00:00:00Z",
        detail="Automated notice and attachment refresh succeeded.",
    )

    assert refreshed["last_success_at"] == "2026-10-04T00:00:00Z"
    assert refreshed["availability_confirmed_at"] == "2026-10-03T00:00:00Z"
    assert refreshed["availability_confirmation_detail"] == (
        "Official status page still accepts applications."
    )
    events = database.list_government_source_verification_events("official-test-source")
    assert events[0]["availability_confirmed_at"] == "2026-10-03T00:00:00Z"
