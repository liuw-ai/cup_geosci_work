from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


@dataclass
class FakeResponse:
    text: str
    url: str


def test_mokahr_adapter_filters_list_jobs_and_keeps_detail_fields(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    listing_url = "https://app.mokahr.com/social-recruitment/zijinmining/140688?locale=zh-CN#/jobs"
    source = {
        "id": "zijin-social-career",
        "publisher": "紫金矿业集团股份有限公司",
        "homepage_url": listing_url,
        "source_type": "mokahr_search",
        "config": {
            "listing_url": listing_url,
            "org_id": "zijinmining",
            "site_id": 140688,
            "mode": "social",
            "include_patterns": ["地质|勘查|物探"],
            "exclude_patterns": ["软件|人力"],
            "fetch_detail_pages": True,
            "require_detail_pages": True,
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }
    initial = """<input id="init-data" value='{"org":{"id":"zijinmining"},"siteId":140688,"mode":"social","aesIv":"1234567890123456"}'>"""
    monkeypatch.setattr(collector, "_get", lambda _url, _source: FakeResponse(initial, listing_url))
    calls: list[str] = []

    def api(url, payload, _iv, _source=None):
        calls.append(url)
        if url.endswith("/jobs/v2"):
            return {"data": {"jobs": [
                {"id": "irrelevant", "title": "软件工程师"},
                {"id": "geo-1", "title": "地质勘探类", "department": {"name": "矿产资源部"}},
            ]}}
        return {"data": {
            "id": "geo-1", "title": "地质勘探类", "education": "硕士",
            "commitment": "全职", "zhineng": {"name": "地质类"},
            "department": {"name": "矿产资源部"}, "locations": [{"name": "福建"}],
            "publishedAt": "2026-09-01T12:00:00",
            "jobDescription": "<p>任职要求：地质、物探、化探相关专业。</p>",
        }}

    monkeypatch.setattr(collector, "_mokahr_api_json", api)
    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].title == "地质勘探类"
    assert postings[0].location == "福建"
    assert postings[0].published_date == "2026-09-01"
    assert "学历：硕士" in postings[0].summary
    assert "物探" in (postings[0].match_text or "")
    assert postings[0].source_url.endswith("#/job/geo-1")
    assert len(calls) == 2


def test_mokahr_adapter_reads_bounded_following_pages(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    listing_url = "https://app.mokahr.com/social-recruitment/zijinmining/140688?locale=zh-CN#/jobs"
    source = {
        "id": "zijin-social-career",
        "publisher": "紫金矿业集团股份有限公司",
        "homepage_url": listing_url,
        "source_type": "mokahr_search",
        "config": {
            "listing_url": listing_url,
            "org_id": "zijinmining",
            "site_id": 140688,
            "mode": "social",
            "include_patterns": ["地质"],
            "fetch_detail_pages": True,
            "require_detail_pages": True,
            "max_items": 2,
            "page_size": 1,
            "max_listing_pages": 2,
            "request_interval_seconds": 0,
        },
    }
    initial = """<input id="init-data" value='{"org":{"id":"zijinmining"},"siteId":140688,"mode":"social","aesIv":"1234567890123456"}'>"""
    monkeypatch.setattr(collector, "_get", lambda _url, _source: FakeResponse(initial, listing_url))
    requested_offsets: list[int] = []

    def api(url, payload, _iv, _source=None):
        if url.endswith("/jobs/v2"):
            requested_offsets.append(payload["offset"])
            job_id = "geo-1" if payload["offset"] == 0 else "geo-2"
            return {"data": {"jobs": [{"id": job_id, "title": "地质工程师"}]}}
        job_id = payload["jobId"]
        return {"data": {
            "id": job_id,
            "title": "地质工程师",
            "education": "本科及以上",
            "jobDescription": "地质工程专业。",
        }}

    monkeypatch.setattr(collector, "_mokahr_api_json", api)

    postings = collector.collect(source)

    assert requested_offsets == [0, 1]
    assert [posting.external_id for posting in postings] == ["geo-1", "geo-2"]


def test_mokahr_aes_payload_is_decrypted() -> None:
    key = b"0123456789abcdef"
    iv = "1234567890123456"
    clear = json.dumps({"success": True, "data": {"jobs": []}}).encode("utf-8")
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(clear) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv.encode("utf-8")))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    payload = {"data": base64.b64encode(encrypted).decode("ascii"), "necromancer": key.decode("ascii")}

    decoded = OfficialSourceCollector._decrypt_mokahr_payload(payload, iv)

    assert decoded["success"] is True
    assert decoded["data"]["jobs"] == []


def test_successfactors_title_filters_reject_software_roles() -> None:
    config = {
        "required_title_patterns": ["geology|geophysic|reservoir"],
        "excluded_title_patterns": ["software|developer|platform"],
    }
    assert OfficialSourceCollector._title_matches_filters("Geophysicist", config)
    assert not OfficialSourceCollector._title_matches_filters("Senior Software Engineer", config)
