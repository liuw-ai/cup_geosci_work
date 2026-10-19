from __future__ import annotations

import json
import hashlib
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
        self.request_kwargs: dict[str, dict[str, object]] = {}

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append(url)
        self.request_kwargs[url] = kwargs
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


def test_automatic_after_opening_does_not_request_before_official_window(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    registry = _registry()
    registry["source_opening_at"] = {
        "official-test-source": "2026-10-10T08:00:00+08:00"
    }
    session = NoRequestSession()

    results = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        now=datetime(2026, 10, 9, 23, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "scheduled"
    assert session.calls == 0


def test_automatic_after_opening_recovers_source_without_open_rows(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["id"] = "cea-2027-recruitment"
    source_record["homepage_url"] = (
        "https://www.cea.gov.cn/cea/zwgk/rsxx/ryzp/5855536/index.html"
    )
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "allowed_hosts": ["www.cea.gov.cn"],
        "official_attachment_url": (
            "https://www.cea.gov.cn/cea/zwgk/rsxx/ryzp/5855536/2026092316044245325.xlsx"
        ),
    }
    registry = {
        "version": 1,
        "as_of": "2026-10-12",
        "source_opening_at": {
            "cea-2027-recruitment": "2026-10-10T08:00:00+08:00"
        },
        "records": [],
    }
    session = FakeSession(
        {
            "https://www.cea.gov.cn/robots.txt": FakeResponse(
                200, b"User-agent: *\nDisallow: /\n"
            )
        }
    )

    results = revalidate_government_sources(
        registry,
        {"cea-2027-recruitment": source_record},
        settings,
        now=datetime(2026, 10, 12, 2, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "source_unavailable"
    assert "robots.txt does not permit" in results[0]["detail"]
    assert session.calls == ["https://www.cea.gov.cn/robots.txt"]


def test_automatic_after_opening_missing_instant_never_requests_official_site(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    registry = _registry()
    registry["source_opening_dates"] = {"official-test-source": "2026-10-10"}
    session = NoRequestSession()

    results = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        now=datetime(2026, 10, 11, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "source_unavailable"
    assert "source_opening_at" in results[0]["detail"]
    assert session.calls == 0


def test_automatic_after_opening_downloads_and_verifies_full_attachment_hash(tmp_path) -> None:
    settings = make_settings(tmp_path)
    attachment = b"PK\x03\x04official-position-table"
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "allowed_hosts": ["careers.example.edu.cn"],
        "expected_attachment_sha256": hashlib.sha256(attachment).hexdigest(),
    }
    registry = _registry()
    registry["source_opening_at"] = {
        "official-test-source": "2026-09-27T08:00:00+08:00"
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(200, attachment),
        }
    )

    results = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        now=datetime(2026, 9, 28, 2, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "verified"
    assert results[0]["evidence_fingerprint"]
    assert session.responses["https://careers.example.edu.cn/notice/1.xlsx"].yielded_bytes == len(attachment)


def test_full_attachment_hash_range_uses_configured_size_limit(tmp_path) -> None:
    settings = make_settings(tmp_path)
    attachment = b"x" * 300_000
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "allowed_hosts": ["careers.example.edu.cn"],
        "expected_attachment_sha256": hashlib.sha256(attachment).hexdigest(),
        "max_attachment_bytes": 512_000,
    }
    registry = _registry()
    registry["source_opening_at"] = {
        "official-test-source": "2026-09-27T08:00:00+08:00"
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(200, attachment),
        }
    )

    results = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        now=datetime(2026, 9, 28, 2, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "verified"
    assert session.request_kwargs["https://careers.example.edu.cn/notice/1.xlsx"]["headers"] == {
        "Range": "bytes=0-511999"
    }


def test_automatic_after_opening_starts_at_exact_opening_instant(tmp_path) -> None:
    """The configured opening instant is inclusive and evaluated in UTC safely."""
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "allowed_hosts": ["careers.example.edu.cn"],
    }
    registry = _registry()
    registry["source_opening_at"] = {
        "official-test-source": "2026-10-10T08:00:00+08:00"
    }
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(
                200, b"PK\x03\x04xlsx"
            ),
        }
    )

    results = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        # 00:00 UTC is exactly 08:00 Asia/Shanghai.
        now=datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "verified"
    assert session.calls == [
        "https://careers.example.edu.cn/robots.txt",
        "https://careers.example.edu.cn/notice/1",
        "https://careers.example.edu.cn/notice/1.xlsx",
    ]


def test_recheck_can_renew_rolling_vacancy_from_explicit_official_status_page(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
        "current_availability_url": "https://careers.example.edu.cn/status/1",
        "availability_open_patterns": [r"当前招聘中"],
        "availability_closed_patterns": [r"已招满|停止报名"],
    }
    registry = _registry()
    registry["records"][0]["deadline_policy"] = "open_until_filled"
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(206, b"PK\x03\x04xlsx"),
            "https://careers.example.edu.cn/status/1": FakeResponse(
                200, "当前招聘中；报名截止前招满即止".encode()
            ),
        }
    )

    results = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        now=datetime(2026, 9, 28, 2, tzinfo=timezone.utc),
        session=session,
    )

    assert results[0]["status"] == "verified"
    assert results[0]["availability_confirmed_at"] == "2026-09-28T02:00:00Z"
    assert "当前招聘中" in results[0]["availability_confirmation_detail"]
    assert session.calls == [
        "https://careers.example.edu.cn/robots.txt",
        "https://careers.example.edu.cn/notice/1",
        "https://careers.example.edu.cn/notice/1.xlsx",
        "https://careers.example.edu.cn/status/1",
    ]


def test_status_page_close_marker_does_not_renew_rolling_vacancy(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "allowed_hosts": ["careers.example.edu.cn"],
        "current_availability_url": "https://careers.example.edu.cn/status/1",
        "availability_open_patterns": [r"当前招聘中"],
        "availability_closed_patterns": [r"已招满|停止报名"],
    }
    registry = _registry()
    registry["records"][0]["deadline_policy"] = "open_until_filled"
    session = FakeSession(
        {
            "https://careers.example.edu.cn/notice/1": FakeResponse(200, b"official notice"),
            "https://careers.example.edu.cn/notice/1.xlsx": FakeResponse(206, b"PK\x03\x04xlsx"),
            "https://careers.example.edu.cn/status/1": FakeResponse(
                200, "当前招聘中，但已招满".encode()
            ),
        }
    )

    result = revalidate_government_sources(
        registry,
        {"official-test-source": source_record},
        settings,
        session=session,
    )[0]

    assert result["status"] == "verified"
    assert "availability_confirmed_at" not in result


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
    assert result["withdrawal_evidence_url"] == "https://careers.example.edu.cn/notice/1"


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


def test_manual_only_source_keeps_reviewed_registry_rows_visible_on_recheck_gap() -> None:
    manual_sources = {"official-test-source"}
    assert len(current_publishable_position_records(
        _registry(),
        today="2026-09-28",
        max_age_hours=None,
        manual_confirmation_source_ids=manual_sources,
    )) == 1

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


def test_worker_can_isolate_a_single_government_source_before_revalidation(
    tmp_path, monkeypatch
) -> None:
    registry_path = tmp_path / "government_positions.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "as_of": "2026-09-28",
                "source_opening_at": {
                    "cea-2027-recruitment": "2026-09-27T08:00:00+08:00",
                    "other-government-source": "2026-09-27T08:00:00+08:00",
                },
                "records": [
                    {
                        "id": "cea-position",
                        "source_id": "cea-2027-recruitment",
                        "position_type": "public_institution",
                        "province": "测试省",
                        "employer": "测试单位",
                        "position_code": "CEA-001",
                        "title": "地质技术岗",
                        "major_requirement": "地质学",
                        "degree_requirement": "硕士研究生",
                        "location": "测试市",
                        "headcount": 1,
                        "deadline_date": "2026-12-31",
                        "deadline_policy": "fixed_date",
                        "official_notice_url": "https://cea.example/notice",
                        "official_attachment_url": "https://cea.example/notice.xlsx",
                        "evidence_locator": "岗位表!2",
                        "record_status": "verified_open",
                        "match_status": "explicit_match",
                    },
                    {
                        "id": "other-position",
                        "source_id": "other-government-source",
                        "position_type": "public_institution",
                        "province": "测试省",
                        "employer": "其他单位",
                        "position_code": "OTHER-001",
                        "title": "地质技术岗",
                        "major_requirement": "地质学",
                        "degree_requirement": "硕士研究生",
                        "location": "测试市",
                        "headcount": 1,
                        "deadline_date": "2026-12-31",
                        "deadline_policy": "fixed_date",
                        "official_notice_url": "https://other.example/notice",
                        "official_attachment_url": "https://other.example/notice.xlsx",
                        "evidence_locator": "岗位表!2",
                        "record_status": "verified_open",
                        "match_status": "explicit_match",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    cea = source()
    cea["id"] = "cea-2027-recruitment"
    cea["config"] = {"government_evidence_recheck": True}
    other = source()
    other["id"] = "other-government-source"
    other["config"] = {"government_evidence_recheck": True}
    worker.database.upsert_source(cea)
    worker.database.upsert_source(other)
    observed: dict[str, object] = {}

    def fake_revalidate(registry, sources_by_id, _settings, *, now):
        observed["source_ids"] = set(sources_by_id)
        observed["record_source_ids"] = {
            str(record["source_id"]) for record in registry["records"]
        }
        observed["opening_source_ids"] = set(registry["source_opening_at"])
        return [
            {
                "source_id": "cea-2027-recruitment",
                "status": "source_unavailable",
                "checked_at": "2026-09-28T01:00:00Z",
                "detail": "robots denied",
                "evidence_fingerprint": "",
            }
        ]

    monkeypatch.setattr("job_hub.worker.revalidate_government_sources", fake_revalidate)

    result = worker._revalidate_government_position_sources(
        only_source_ids={"cea-2027-recruitment"}
    )

    assert result["source_unavailable"] == 1
    assert observed == {
        "source_ids": {"cea-2027-recruitment"},
        "record_source_ids": {"cea-2027-recruitment"},
        "opening_source_ids": {"cea-2027-recruitment"},
    }
    events = worker.database.list_government_source_verification_events()
    assert [event["source_id"] for event in events] == ["cea-2027-recruitment"]


def test_worker_persists_automated_availability_confirmation(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "government_positions.json"
    registry = {
        "version": 1,
        "as_of": "2026-09-28",
        "records": [
            {
                "id": "official-test-position",
                "source_id": "official-test-source",
                "position_type": "public_institution",
                "province": "测试省",
                "employer": "测试单位",
                "position_code": "A-001",
                "title": "地质技术岗",
                "major_requirement": "地质学",
                "degree_requirement": "硕士研究生",
                "location": "测试市",
                "headcount": 1,
                "deadline_date": "",
                "deadline_policy": "open_until_filled",
                "official_notice_url": "https://careers.example.edu.cn/notice/1",
                "official_attachment_url": "https://careers.example.edu.cn/notice/1.xlsx",
                "evidence_locator": "岗位表!2",
                "record_status": "verified_open",
                "match_status": "explicit_match",
            }
        ],
    }
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False), encoding="utf-8"
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "current_availability_url": "https://careers.example.edu.cn/status/1",
    }
    worker.database.upsert_source(source_record)
    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [
            {
                "source_id": "official-test-source",
                "status": "verified",
                "checked_at": "2026-09-28T01:00:00Z",
                "detail": "official current status page matched",
                "evidence_fingerprint": "abc",
                "availability_confirmed_at": "2026-09-28T01:00:00Z",
                "availability_confirmation_detail": "官方当前状态页匹配开放标记",
            }
        ],
    )

    result = worker._revalidate_government_position_sources()
    verification = worker.database.list_government_source_verifications()[0]

    assert result["verified"] == 1
    assert verification["availability_confirmed_at"] == "2026-09-28T01:00:00Z"
    assert "开放标记" in verification["availability_confirmation_detail"]


def test_worker_enables_automatic_source_after_two_verified_refreshes(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "government_positions.json"
    registry_path.write_text(
        '{"version": 1, "as_of": "2026-09-28", "records": []}',
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    source_record = source()
    source_record["enabled"] = False
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "runtime_enabled_control": True,
        "activation_required_successes": 2,
        "max_age_hours": 48,
        "expected_attachment_sha256": "a" * 64,
    }
    worker.database.upsert_source(source_record)
    monkeypatch.setattr(
        "job_hub.worker.load_position_registry",
        lambda _path: {
            "source_opening_at": {
                "official-test-source": "2026-09-27T08:00:00+08:00"
            },
            "records": [],
        },
    )
    worker.database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at="2026-09-28T00:00:00Z",
        detail="first complete refresh",
    )
    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [
            {
                "source_id": "official-test-source",
                "status": "verified",
                "checked_at": "2026-09-28T01:00:00Z",
                "detail": "second complete refresh",
                "evidence_fingerprint": "abc",
            }
        ],
    )

    result = worker._revalidate_government_position_sources()

    assert result["verified"] == 1
    assert result["activated"] == 1
    assert worker.database.get_source("official-test-source")["enabled"] is True


def test_preopening_successes_cannot_activate_automatic_source(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "government_positions.json"
    registry_path.write_text(
        '{"version": 1, "as_of": "2026-10-09", "records": []}',
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    source_record = source()
    source_record["enabled"] = False
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "runtime_enabled_control": True,
        "activation_required_successes": 2,
        "expected_attachment_sha256": "a" * 64,
    }
    worker.database.upsert_source(source_record)
    monkeypatch.setattr(
        "job_hub.worker.load_position_registry",
        lambda _path: {
            "source_opening_at": {
                "official-test-source": "2026-10-10T08:00:00+08:00"
            },
            "records": [],
        },
    )
    for checked_at in ("2026-10-02T16:28:54Z", "2026-10-02T17:11:34Z"):
        worker.database.record_government_source_verification(
            "official-test-source", status="verified", checked_at=checked_at
        )
    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [{
            "source_id": "official-test-source",
            "status": "verified",
            "checked_at": "2026-10-10T01:00:00Z",
            "detail": "first post-opening official refresh",
            "evidence_fingerprint": "abc",
        }],
    )

    first = worker._revalidate_government_position_sources()
    assert first["activated"] == 0
    assert worker.database.get_source("official-test-source")["enabled"] is False

    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [{
            "source_id": "official-test-source",
            "status": "verified",
            "checked_at": "2026-10-10T02:00:00Z",
            "detail": "second post-opening official refresh",
            "evidence_fingerprint": "abc",
        }],
    )
    second = worker._revalidate_government_position_sources()
    assert second["activated"] == 1
    assert worker.database.get_source("official-test-source")["enabled"] is True


def test_first_automatic_refresh_never_publishes_before_activation(tmp_path, monkeypatch) -> None:
    """A first post-opening success records evidence but cannot reach students."""
    registry_path = tmp_path / "government_positions.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "as_of": "2026-10-09",
                "source_opening_at": {
                    "official-test-source": "2026-10-10T08:00:00+08:00"
                },
                "records": [
                    {
                        "id": "official-test-position",
                        "source_id": "official-test-source",
                        "position_type": "public_institution",
                        "province": "北京市",
                        "employer": "测试单位",
                        "position_code": "A-001",
                        "title": "地质技术岗",
                        "major_requirement": "地质学",
                        "degree_requirement": "硕士研究生",
                        "location": "北京市",
                        "headcount": 1,
                        "deadline_date": "2099-12-31",
                        "deadline_policy": "fixed_date",
                        "official_notice_url": "https://careers.example.edu.cn/notice/1",
                        "official_attachment_url": "https://careers.example.edu.cn/notice/1",
                        "evidence_locator": "公告正文岗位 A-001",
                        "record_status": "verified_open",
                        "match_status": "explicit_match",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    source_record = source()
    source_record["enabled"] = False
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "runtime_enabled_control": True,
        "activation_required_successes": 2,
        "max_age_hours": 48,
        "expected_attachment_sha256": "a" * 64,
    }
    worker.database.upsert_source(source_record)

    def verified_refresh(*_args, **_kwargs):
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return [
            {
                "source_id": "official-test-source",
                "status": "verified",
                "checked_at": checked_at,
                "detail": "complete official refresh",
                "evidence_fingerprint": "abc",
            }
        ]

    monkeypatch.setattr("job_hub.worker.revalidate_government_sources", verified_refresh)

    first_verification, first_jobs = worker._refresh_government_evidence()
    first_rows, _ = worker.database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    assert first_verification["verified"] == 1
    assert first_verification["activated"] == 0
    assert first_jobs["created"] == 0
    assert first_rows == []
    assert worker.database.get_source("official-test-source")["enabled"] is False
    first_capture = worker.database.get_source_capture_freshness("official-test-source")
    assert first_capture is not None
    assert first_capture["evidence_kind"] == "government_evidence_recheck"

    second_verification, second_jobs = worker._refresh_government_evidence()
    second_rows, _ = worker.database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    assert second_verification["activated"] == 1
    assert second_jobs["created"] == 1
    assert len(second_rows) == 1
    assert worker.database.get_source("official-test-source")["enabled"] is True


def test_failed_refresh_resets_automatic_activation_streak(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "government_positions.json"
    registry_path.write_text(
        '{"version": 1, "as_of": "2026-09-28", "records": []}',
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path), government_position_registry_path=registry_path
    )
    worker = DailyWorker(settings)
    source_record = source()
    source_record["enabled"] = False
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "runtime_enabled_control": True,
        "activation_required_successes": 2,
        "expected_attachment_sha256": "a" * 64,
    }
    worker.database.upsert_source(source_record)
    monkeypatch.setattr(
        "job_hub.worker.load_position_registry",
        lambda _path: {
            "source_opening_at": {
                "official-test-source": "2026-09-27T08:00:00+08:00"
            },
            "records": [],
        },
    )
    worker.database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at="2026-09-28T00:00:00Z",
        detail="first complete refresh",
    )
    worker.database.record_government_source_verification(
        "official-test-source",
        status="source_unavailable",
        checked_at="2026-09-28T00:30:00Z",
        detail="temporary official portal failure",
    )
    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [
            {
                "source_id": "official-test-source",
                "status": "verified",
                "checked_at": "2026-09-28T01:00:00Z",
                "detail": "one verified refresh after failure",
                "evidence_fingerprint": "abc",
            }
        ],
    )

    result = worker._revalidate_government_position_sources()

    assert result["verified"] == 1
    assert result["activated"] == 0
    assert worker.database.get_source("official-test-source")["enabled"] is False


def test_source_recheck_failure_preserves_last_success_without_visibility_expiry(tmp_path) -> None:
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
    assert len(expired) == 1


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


def test_unconfigured_source_does_not_hide_reviewed_official_rows() -> None:
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

    assert len(records) == 1


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
