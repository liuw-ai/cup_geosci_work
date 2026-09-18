from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import zlib

from bs4 import BeautifulSoup

from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


@dataclass
class FakeResponse:
    text: str
    url: str


def test_cupb_adapter_keeps_vacancies_and_skips_recruitment_events(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "三桶油与油服",
        "source_tier": "B",
        "config": {
            "detail_path_patterns": ["/campus/view/id/"],
            "require_recruitment_word": True,
            "exclude_patterns": ["招聘会", "宣讲会"],
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }
    homepage = "https://career.cup.edu.cn/"
    vacancy_url = "https://career.cup.edu.cn/campus/view/id/101"
    responses = {
        homepage: FakeResponse(
            text=(
                '<a href="/campus/view/id/101">某油田 2027 届校园招聘</a>'
                '<a href="/campus/view/id/102">秋季招聘会</a>'
            ),
            url=homepage,
        ),
        vacancy_url: FakeResponse(
            text=(
                '<div class="details-title"><h5>某油田 2027 届校园招聘</h5></div>'
                '<main class="zp-details">面向地质工程、资源勘查工程本科和硕士毕业生。'
                '报名截止时间为2026年12月10日。</main>'
            ),
            url=vacancy_url,
        ),
    }

    def get(url, _source):
        return responses[url]

    monkeypatch.setattr(collector, "_get", get)

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].title == "某油田 2027 届校园招聘"
    assert postings[0].source_url == vacancy_url
    assert postings[0].deadline_date == "2026-12-10"


def test_cupb_adapter_uses_structured_job_table_as_matching_evidence(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "三桶油与油服",
        "source_tier": "B",
        "config": {
            "detail_path_patterns": ["/campus/view/id/"],
            "exclude_patterns": ["招聘会", "宣讲会"],
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }
    homepage = "https://career.cup.edu.cn/"
    vacancy_url = "https://career.cup.edu.cn/campus/view/id/102"
    responses = {
        homepage: FakeResponse(
            text='<a href="/campus/view/id/102">某油田校园招聘</a>',
            url=homepage,
        ),
        vacancy_url: FakeResponse(
            text=(
                '<div class="details-title"><h5>某油田校园招聘</h5></div>'
                '<main class="zp-details">单位长期从事地球物理勘探。'
                '<table><tr><td>岗位</td><td>物探地质研发岗</td></tr>'
                '<tr><td>专业范围</td><td>地质工程、资源勘查工程</td></tr>'
                '<tr><td>面向对象</td><td>2027届硕士、博士毕业生</td></tr>'
                '<tr><td>工作地点</td><td>北京、成都</td></tr></table>'
                '报名截止时间为2026年12月10日。</main>'
            ),
            url=vacancy_url,
        ),
    }

    def get(url, _source):
        return responses[url]

    monkeypatch.setattr(collector, "_get", get)

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].location == "北京、成都"
    assert postings[0].summary.startswith("岗位：物探地质研发岗")
    assert "地质工程" in (postings[0].match_text or "")
    assert "单位长期从事" not in (postings[0].match_text or "")


def test_cupb_adapter_decodes_public_embedded_announcement_content(tmp_path) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "三桶油与油服",
        "source_tier": "B",
        "config": {"exclude_patterns": ["招聘会", "宣讲会"]},
    }
    article_html = (
        '<table><tr><td>岗位</td><td>物探地质研发岗</td></tr>'
        '<tr><td>专业范围</td><td>地质工程、资源勘查工程</td></tr>'
        '<tr><td>面向对象</td><td>2027届硕士、博士毕业生</td></tr>'
        '<tr><td>工作地点</td><td>北京、成都</td></tr></table>'
        '<a href="https://official.example.cn/apply">中国石油招聘平台</a>'
    )
    encoded_article = base64.b64encode(
        f"view1d {article_html}".encode("utf-8")
    ).decode("ascii")
    payload = base64.b64encode(
        zlib.compress(f"view2d {encoded_article}".encode("utf-8"))
    ).decode("ascii")
    document = (
        '<div class="details-title"><h5>某油田校园招聘</h5></div>'
        '<div class="zp-details common-view">发布时间：2026年09月17日</div>'
        '<div class="aContent"><section id="content1"></section></div>'
        f'<script>$("#content1").parent().html(Base64.decode(unzip("{payload}").substr(6)).substr(6));</script>'
    )

    posting = collector._extract_cupb_detail(
        document,
        "https://career.cup.edu.cn/campus/view/id/103",
        source,
        "某油田校园招聘",
    )

    assert posting is not None
    assert posting.location == "北京、成都"
    assert posting.application_url == "https://official.example.cn/apply"
    assert "资源勘查工程" in (posting.match_text or "")
    assert "硕士" in posting.text


def test_cas_adapter_reads_employer_location_and_detail_fields(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cas-job-board",
        "name": "中国科学院人才招聘网",
        "publisher": "中国科学院",
        "homepage_url": "http://job.cas.cn/",
        "source_type": "cas_job_board",
        "category": "科研院所与高校",
        "source_tier": "A",
        "config": {"max_items": 10, "request_interval_seconds": 0},
    }
    homepage = "http://job.cas.cn/"
    detail_url = (
        "http://job.cas.cn/bns/resume/ResumeSubmit/toRecruitment.do?"
        "postInfo.id=13900&webOrPersonFlag=2"
    )
    responses = {
        homepage: FakeResponse(
            text=(
                "<table><tr><td><a href=\"/bns/resume/ResumeSubmit/"
                "toRecruitment.do?postInfo.id=13900&webOrPersonFlag=2\">"
                "地质工程科研助理</a></td><td>中国科学院测试研究所</td>"
                "<td>北京</td></tr></table>"
            ),
            url=homepage,
        ),
        detail_url: FakeResponse(
            text=(
                "<h2>地质工程科研助理</h2><main>工作地点：北京 学历要求：硕士 "
                "招聘人数：2 发布时间：2026年09月15日 截止时间：2026年10月30日 "
                "学科领域：地质资源与地质工程 专业描述：地球科学相关专业。</main>"
            ),
            url=detail_url,
        ),
    }

    def get(url, _source):
        return responses[url]

    monkeypatch.setattr(collector, "_get", get)

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].title == "地质工程科研助理"
    assert postings[0].employer == "中国科学院测试研究所"
    assert postings[0].location == "北京"
    assert postings[0].published_date == "2026-09-15"
    assert postings[0].deadline_date == "2026-10-30"
    assert "地质资源与地质工程" in (postings[0].match_text or "")


def test_halliburton_adapter_preserves_official_job_url_and_search_topic(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "halliburton-career",
        "name": "Halliburton Careers",
        "publisher": "Halliburton",
        "homepage_url": "https://jobs.halliburton.com/",
        "source_type": "successfactors_search",
        "category": "在华外企与国际机会",
        "source_tier": "A",
        "config": {
            "search_url": "https://jobs.halliburton.com/search/",
            "locale": "en_US",
            "queries": ["geology"],
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }
    search_url = "https://jobs.halliburton.com/search/?q=geology&locale=en_US"
    responses = {
        search_url: FakeResponse(
            text=(
                '<table><tr class="data-row"><td><a class="jobTitle-link" '
                'href="/job/Houston-Geologist-TX-77001/12345/">Geologist</a></td>'
                '<td class="colLocation"><span class="jobLocation">Houston, TX, US</span>'
                "</td></tr></table>"
            ),
            url=search_url,
        )
    }

    def get(url, _source):
        return responses[url]

    monkeypatch.setattr(collector, "_get", get)

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].employer == "Halliburton"
    assert postings[0].location == "Houston, TX, US"
    assert postings[0].source_url == (
        "https://jobs.halliburton.com/job/Houston-Geologist-TX-77001/12345/"
    )
    assert "Search topic: geology" in postings[0].text
    assert "Search topic" not in (postings[0].match_text or "")


def test_halliburton_adapter_reads_public_detail_page_when_enabled(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "halliburton-career",
        "name": "Halliburton Careers",
        "publisher": "Halliburton",
        "homepage_url": "https://jobs.halliburton.com/",
        "source_type": "successfactors_search",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "config": {
            "search_url": "https://jobs.halliburton.com/search/",
            "locale": "en_US",
            "queries": ["geology"],
            "max_items": 10,
            "request_interval_seconds": 0,
            "fetch_detail_pages": True,
            "detail_content_selector": ".jobDisplay",
            "detail_location_selector": "#job-location",
        },
    }
    search_url = "https://jobs.halliburton.com/search/?q=geology&locale=en_US"
    detail_url = "https://jobs.halliburton.com/job/Houston-Geologist-TX-77001/12345/"
    responses = {
        search_url: FakeResponse(
            text=(
                '<table><tr class="data-row"><td><a class="jobTitle-link" '
                'href="/job/Houston-Geologist-TX-77001/12345/">Geologist</a></td>'
                '<td class="colLocation"><span class="jobLocation">Houston, TX, US</span>'
                "</td></tr></table>"
            ),
            url=search_url,
        ),
        detail_url: FakeResponse(
            text=(
                '<div class="jobDisplay"><h1>Geologist</h1>'
                '<span id="job-location">Houston, TX, US</span>'
                '<h2>Job Duties</h2><p>Interpret seismic and reservoir data.</p>'
                '<h2>Qualifications</h2><p>Master degree in Geology or Geophysics. '
                'Application deadline: December 31, 2026.</p>'
                '<a href="/talentcommunity/apply/12345/">Apply now</a></div>'
            ),
            url=detail_url,
        ),
    }

    def get(url, _source):
        return responses[url]

    monkeypatch.setattr(collector, "_get", get)

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].location == "Houston, TX, US"
    assert postings[0].deadline_date == "2026-12-31"
    assert "Interpret seismic" in postings[0].text
    assert "Geophysics" in (postings[0].match_text or "")
    assert postings[0].application_url == (
        "https://jobs.halliburton.com/talentcommunity/apply/12345/"
    )


def test_attachment_is_not_treated_as_an_application_url(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    attachment = BeautifulSoup(
        '<p>附件：<a href="/files/application-form.xlsx">应聘登记表</a></p>',
        "html.parser",
    )
    live_application = BeautifulSoup(
        '<p><a href="/apply/123">立即报名</a></p>',
        "html.parser",
    )

    assert collector._find_application_url(
        attachment, "https://official.example.cn/jobs/1"
    ) is None
    assert collector._find_application_url(
        live_application, "https://official.example.cn/jobs/1"
    ) == "https://official.example.cn/apply/123"

def test_selector_order_preserves_configured_priority(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    soup = BeautifulSoup("<body><div id='specific'>正文</div></body>", "html.parser")
    selected = collector._select_first(soup, "#specific, body")
    assert selected is not None and selected.get("id") == "specific"

def test_html_notice_prefers_first_content_selector(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "beijing-hrss",
        "publisher": "北京市人力资源和社会保障局",
        "source_type": "html_notice",
        "config": {
            "require_recruitment_word": True,
            "exclude_patterns": ["\u804c\u79f0"],
            "detail_exclude_patterns": [],
            "title_selector": "#mainText h1, h1",
            "content_selector": "#mainTextZoom .view, #mainTextZoom, article, main, body",
        },
    }
    document = (
        "<html><body><header>网站导航</header>"
        "<div id='mainText'><h1>某事业单位公开招聘</h1>"
        "<div id='mainTextZoom'><div class='view'>2026年公开招聘地质工程岗位，报名截止时间为2026年12月31日。</div>"
        "\u804c\u79f0要求。"
        "</div></div></body></html>"
    )
    posting = collector._extract_html_detail(
        document, "https://rsj.beijing.gov.cn/xxgk/gkzp/202609/t1.html", source, "公告"
    )
    assert posting is not None
    assert posting.title == "某事业单位公开招聘"
    assert "地质工程" in posting.summary
    assert "网站导航" not in posting.summary
