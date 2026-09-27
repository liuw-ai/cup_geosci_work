from __future__ import annotations

from datetime import datetime, timezone

from job_hub.db import Database
from job_hub.government_positions import current_publishable_position_records
from job_hub.government_revalidation import revalidate_government_sources

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

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        return self.responses[url]


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
