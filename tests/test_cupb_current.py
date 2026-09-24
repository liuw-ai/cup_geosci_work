from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


FIXTURE = Path(__file__).parent / "fixtures" / "domestic" / "cupb_beibu_bay_2027.html"
NOTICE_URL = "https://career.cup.edu.cn/campus/view/id/460523"


@dataclass
class FakeResponse:
    text: str
    url: str


def test_cupb_current_notice_reads_outer_job_table_fields(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    document = FIXTURE.read_text(encoding="utf-8")
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {
            "listing_urls": ["https://career.cup.edu.cn/empty"],
            "direct_notice_urls": [
                {"url": NOTICE_URL, "title_hint": "广西北部湾投资集团2027届高校毕业生校园招聘公告"}
            ],
            "detail_path_patterns": ["/campus/view/id/"],
            "exclude_patterns": ["招聘会", "宣讲会"],
            "require_detail_content": True,
            "minimum_detail_characters": 80,
            "max_items": 10,
            "candidate_limit": 10,
            "request_interval_seconds": 0,
        },
    }

    def get(url, _source):
        if url == "https://career.cup.edu.cn/empty":
            return FakeResponse("<html><body>empty</body></html>", url)
        return FakeResponse(document, url)

    collector._get = get  # type: ignore[method-assign]
    postings = collector.collect(source)

    assert len(postings) == 1
    posting = postings[0]
    assert posting.title.startswith("“向新出发 无限未来”广西北部湾投资集团")
    assert posting.employer == "广西北部湾投资集团有限公司"
    assert posting.location == "广西南宁市青秀区"
    assert posting.published_date == "2026-09-22"
    assert posting.deadline_date == "2026-12-21"
    # The outer position table is the role-level evidence.  The long
    # announcement body can mention other disciplines, but must not be used
    # to relabel this storage/materials/IT role as a geoscience vacancy.
    assert "储能科学与工程" in (posting.match_text or "")
    assert "资源勘查工程" not in (posting.match_text or "")
    assert "本科" in posting.summary
    assert posting.source_url == NOTICE_URL
