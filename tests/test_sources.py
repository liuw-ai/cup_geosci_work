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


@dataclass
class FakeJsonResponse:
    payload: object

    def json(self) -> object:
        return self.payload


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


def test_cupb_adapter_scans_past_irrelevant_listing_cards(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {
            "detail_path_patterns": ["/campus/view/id/"],
            "exclude_patterns": ["招聘会", "宣讲会"],
            "max_items": 1,
            "candidate_limit": 4,
            "request_interval_seconds": 0,
        },
    }
    homepage = "https://career.cup.edu.cn/"
    irrelevant_url = "https://career.cup.edu.cn/campus/view/id/110"
    relevant_url = "https://career.cup.edu.cn/campus/view/id/111"
    responses = {
        homepage: FakeResponse(
            text=(
                f'<a href="{irrelevant_url}">某企业岗位</a>'
                f'<a href="{relevant_url}">某地勘单位招聘</a>'
            ),
            url=homepage,
        ),
        irrelevant_url: FakeResponse(
            text='<div class="details-title"><h5>某企业岗位</h5></div>'
            '<main class="zp-details">公司介绍和福利信息。</main>',
            url=irrelevant_url,
        ),
        relevant_url: FakeResponse(
            text='<div class="details-title"><h5>某地勘单位招聘</h5></div>'
            '<main class="zp-details">招聘地质工程、资源勘查工程硕士毕业生。</main>',
            url=relevant_url,
        ),
    }

    monkeypatch.setattr(collector, "_get", lambda url, _source: responses[url])

    postings = collector.collect(source)

    assert len(postings) == 1


def test_cupb_adapter_splits_each_structured_position_row(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {
            "listing_urls": [],
            "direct_notice_urls": [
                {
                    "url": "https://career.cup.edu.cn/campus/view/id/999",
                    "title_hint": "某单位2027年校园招聘公告",
                }
            ],
            "detail_path_patterns": ["/campus/view/id/"],
            "require_detail_content": True,
            "minimum_detail_characters": 20,
            "max_items": 10,
            "candidate_limit": 10,
            "request_interval_seconds": 0,
        },
    }
    document = """
    <html><head><title>某单位2027年校园招聘公告</title></head><body>
      <h1>某单位2027年校园招聘公告</h1>
      <p>现面向高校毕业生公开招聘。</p>
      <table>
        <tr><th>职位信息</th><th>需求专业</th><th>学历</th><th>工作地点</th></tr>
        <tr><td>地质工程师</td><td>地质工程</td><td>硕士</td><td>北京</td></tr>
        <tr><td>资源勘查岗</td><td>资源勘查工程</td><td>本科</td><td>新疆</td></tr>
      </table>
    </body></html>
    """

    collector._get = lambda _url, _source: FakeResponse(  # type: ignore[method-assign]
        document, "https://career.cup.edu.cn/campus/view/id/999"
    )

    postings = collector.collect(source)

    assert [posting.title for posting in postings] == ["地质工程师", "资源勘查岗"]
    assert [posting.location for posting in postings] == ["北京", "新疆"]
    assert [posting.match_text for posting in postings] == [
        "地质工程师 地质工程 硕士",
        "资源勘查岗 资源勘查工程 本科",
    ]
    assert postings[0].field_evidence["专业范围"] == "地质工程"
    assert postings[1].field_evidence["专业范围"] == "资源勘查工程"
    assert postings[0].qualification_text == postings[0].summary


def test_cupb_adapter_chooses_full_recruitment_table_and_ignores_contest_terms(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {"require_detail_content": True, "minimum_detail_characters": 20},
    }
    document = """
    <html><body>
      <h1>某石化公司2027年招聘公告</h1>
      <main class="zp-details">
        <p>获得全国油气地质大赛、勘探地球物理大赛奖励的毕业生优先。</p>
        <table>
          <tr><th>序号</th><th>招聘岗位</th><th>人数</th><th>学历要求</th><th>工作地点</th><th>专业</th></tr>
          <tr><td>1</td><td>炼化设备技术</td><td>2</td><td>本科、硕士</td><td>呼和浩特市</td><td>机械工程、化工过程机械</td></tr>
          <tr><td>2</td><td>财务审计</td><td>1</td><td>本科</td><td>呼和浩特市</td><td>会计学、审计学</td></tr>
        </table>
        <table>
          <tr><th>序号</th><th>职位信息</th><th>需求专业</th><th>操作</th></tr>
          <tr><td>01</td><td>炼化设备技术</td><td>机械工程、化工过程机械</td><td>投递</td></tr>
        </table>
      </main>
    </body></html>
    """

    postings = collector._extract_cupb_details(
        document,
        "https://career.cup.edu.cn/campus/view/id/460401",
        source,
        "某石化公司2027年招聘公告",
    )

    assert [posting.title for posting in postings] == ["炼化设备技术", "财务审计"]
    assert postings[0].field_evidence["专业范围"] == "机械工程、化工过程机械"
    assert postings[1].field_evidence["专业范围"] == "会计学、审计学"
    assert all("油气地质" not in (posting.match_text or "") for posting in postings)
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


def test_cupb_single_role_table_keeps_row_level_evidence(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {"request_interval_seconds": 0},
    }
    document = """
    <h1>某单位校园招聘公告</h1>
    <main class="zp-details">
      <table>
        <tr><td>岗位</td><td>地质工程师</td></tr>
        <tr><td>专业要求</td><td>地质工程</td></tr>
        <tr><td>学历要求</td><td>本科或以上学历</td></tr>
        <tr><td>工作地点</td><td>北京</td></tr>
      </table>
    </main>
    """

    postings = collector._extract_cupb_details(
        document,
        "https://career.cup.edu.cn/campus/view/id/1001",
        source,
        "某单位校园招聘公告",
    )

    assert len(postings) == 1
    assert postings[0].title == "地质工程师"
    assert postings[0].field_evidence["evidence_scope"] == "official_html_table_row"
    assert postings[0].field_evidence["岗位"] == postings[0].title
    assert postings[0].field_evidence["专业范围"] == "地质工程"


def test_cupb_parallel_role_columns_are_never_merged(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {"request_interval_seconds": 0},
    }
    document = """
    <h1>某单位校园招聘公告</h1>
    <main class="zp-details">
      <table>
        <tr><td>岗位</td><td>物探地质研发岗</td><td>软件开发与人工智能岗</td></tr>
        <tr><td>专业范围</td><td>地质工程、地质资源与地质工程</td><td>计算机科学与技术、人工智能</td></tr>
        <tr><td>面向对象</td><td colspan="2">硕士、博士</td></tr>
        <tr><td>工作地点</td><td colspan="2">北京、成都</td></tr>
      </table>
    </main>
    """

    postings = collector._extract_cupb_details(
        document,
        "https://career.cup.edu.cn/campus/view/id/1002",
        source,
        "某单位校园招聘公告",
    )

    assert [posting.title for posting in postings] == [
        "物探地质研发岗",
        "软件开发与人工智能岗",
    ]
    assert postings[0].field_evidence["专业范围"] == "地质工程、地质资源与地质工程"
    assert postings[1].field_evidence["专业范围"] == "计算机科学与技术、人工智能"
    assert all(posting.field_evidence["面向对象"] == "硕士、博士" for posting in postings)


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


def test_cupb_adapter_decodes_listing_and_follows_next_page(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {
            "listing_urls": ["https://career.cup.edu.cn/campus"],
            "detail_path_patterns": ["/campus/view/id/"],
            "max_items": 1,
            "candidate_limit": 4,
            "listing_page_limit": 2,
            "request_interval_seconds": 0,
        },
    }
    page_one = "https://career.cup.edu.cn/campus"
    page_two = "https://career.cup.edu.cn/campus/index/domain/cup/city//page/2"
    irrelevant_url = "https://career.cup.edu.cn/campus/view/id/201"
    relevant_url = "https://career.cup.edu.cn/campus/view/id/202"

    def encoded(fragment: str) -> str:
        inner = base64.b64encode(f"view1d {fragment}".encode("utf-8")).decode("ascii")
        payload = base64.b64encode(zlib.compress(f"view2d {inner}".encode("utf-8"))).decode(
            "ascii"
        )
        return f'<script>Base64.decode(unzip("{payload}"))</script>'

    responses = {
        page_one: FakeResponse(
            text=encoded(
                f'<a href="{irrelevant_url}">普通企业岗位</a>'
                f'<a href="{page_two}">下一页</a>'
            ),
            url=page_one,
        ),
        page_two: FakeResponse(
            text=encoded(f'<a href="{relevant_url}">地勘单位招聘</a>'),
            url=page_two,
        ),
        irrelevant_url: FakeResponse(
            text=(
                '<div class="details-title"><h5>普通企业岗位</h5></div>'
                '<main class="zp-details">公司介绍和福利信息。</main>'
            ),
            url=irrelevant_url,
        ),
        relevant_url: FakeResponse(
            text=(
                '<div class="details-title"><h5>地勘单位招聘</h5></div>'
                '<main class="zp-details">招聘地质工程、资源勘查工程硕士毕业生。</main>'
            ),
            url=relevant_url,
        ),
    }

    monkeypatch.setattr(collector, "_get", lambda url, _source: responses[url])

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].source_url == relevant_url


def test_cupb_adapter_keeps_free_form_major_evidence_and_real_employer(tmp_path) -> None:
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    source = {
        "id": "cupb-career",
        "name": "中国石油大学（北京）就业信息网",
        "publisher": "中国石油大学（北京）",
        "homepage_url": "https://career.cup.edu.cn/",
        "source_type": "cupb_career",
        "category": "能源、工程与地学拓展",
        "source_tier": "B",
        "config": {
            "exclude_patterns": ["招聘会", "宣讲会", "通知"],
            "request_interval_seconds": 0,
        },
    }
    document = (
        '<title>赣南实验室2026年招聘公告</title>'
        '<div class="title-message"><span class="name">前锦网络信息技术（上海）有限公司</span></div>'
        '<div class="common-view">发布时间：2026年09月20日 过期时间：2026年12月19日</div>'
        '<main class="zp-details">'
        '赣南实验室面向关键矿产开发招聘。'
        '关键矿产安全高效开采团队要求地质工程、岩土工程相关博士学位。'
        '工作地点：江西省赣州市 培养机制：项目博士后。'
        '</main>'
    )

    posting = collector._extract_cupb_detail(
        document,
        "https://career.cup.edu.cn/campus/view/id/104",
        source,
        "赣南实验室2026年招聘公告",
    )

    assert posting is not None
    assert posting.employer == "赣南实验室"
    assert posting.deadline_date == "2026-12-19"
    assert posting.location == "江西省赣州市"
    assert "地质工程" in (posting.match_text or "")


def test_cupb_adapter_prefers_announcement_over_position_duplicate() -> None:
    candidates = [
        ("中国石油集团经济技术研究院2026年招聘公告", "https://career.cup.edu.cn/job/view/id/1"),
        ("中国石油集团经济技术研究院2026年招聘公告", "https://career.cup.edu.cn/campus/view/id/2"),
    ]
    assert OfficialSourceCollector._cupb_deduplicate_candidates(candidates) == [candidates[1]]


def test_cupb_adapter_infers_unit_when_portal_account_is_relay() -> None:
    assert (
        OfficialSourceCollector._cupb_employer_from_title(
            "福建省能源石化创新研究院有限责任公司"
        )
        == "福建省能源石化创新研究院有限责任公司"
    )
    assert (
        OfficialSourceCollector._cupb_employer_from_title(
            "赣南实验室2026年招聘公告"
        )
        == "赣南实验室"
    )


def test_cupb_location_evidence_prefers_official_address_over_portal_default() -> None:
    assert (
        OfficialSourceCollector._cupb_location_evidence(
            "桂林理工大学坐落于世界著名山水旅游名城、中国历史文化名城——桂林。"
        )
        == "桂林"
    )
    assert (
        OfficialSourceCollector._cupb_location_evidence(
            "通讯地址：广西桂林市建干路12号，桂林理工大学人事处"
        )
        == "广西桂林市"
    )
    assert (
        OfficialSourceCollector._cupb_location_evidence(
            "赣南实验室注册在江西省赣州市。"
        )
        == "江西省赣州市"
    )


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


def test_html_notice_skips_interview_replacement_announcements(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    listing_url = "https://official.example.cn/notices"
    source = {
        "id": "official-notice",
        "publisher": "测试地质局",
        "homepage_url": listing_url,
        "source_type": "html_notice",
        "config": {
            "listing_urls": [listing_url],
            "allowed_hosts": ["official.example.cn"],
            "listing_selector": "a",
            "require_recruitment_word": True,
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }

    def get(url, _source):
        assert url == listing_url
        return FakeResponse(
            text=(
                '<a href="/notices/replacement.html">'
                '2026年公开招聘进入面试范围人员递补情况公告</a>'
            ),
            url=listing_url,
        )

    monkeypatch.setattr(collector, "_get", get)

    assert collector.collect(source) == []


def test_html_notice_applies_configured_title_filters(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    listing_url = "https://official.example.cn/notices"
    original_url = "https://official.example.cn/notices/original.html"
    source = {
        "id": "official-notice",
        "publisher": "测试地质局",
        "homepage_url": listing_url,
        "source_type": "html_notice",
        "config": {
            "listing_urls": [listing_url],
            "allowed_hosts": ["official.example.cn"],
            "listing_selector": "a",
            "title_selector": "h1",
            "content_selector": "article",
            "require_recruitment_word": True,
            "required_title_patterns": ["招聘公告"],
            "excluded_title_patterns": ["资格条件变更"],
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }
    requested: list[str] = []

    def get(url, _source):
        requested.append(url)
        if url == listing_url:
            return FakeResponse(
                text=(
                    '<a href="/notices/original.html">2026年公开招聘公告</a>'
                    '<a href="/notices/change.html">2026年公开招聘公告（资格条件变更）</a>'
                ),
                url=listing_url,
            )
        if url == original_url:
            return FakeResponse(
                text=(
                    "<h1>2026年公开招聘公告</h1>"
                    "<article>面向地质工程、资源勘查工程毕业生。</article>"
                ),
                url=original_url,
            )
        raise AssertionError(url)

    monkeypatch.setattr(collector, "_get", get)

    postings = collector.collect(source)

    assert [posting.title for posting in postings] == ["2026年公开招聘公告"]
    assert requested == [listing_url, original_url]


def test_mnr_public_api_adapter_uses_public_position_fields(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "mnr-public-recruitment",
        "name": "自然资源部所属企事业单位公开招聘平台",
        "publisher": "中华人民共和国自然资源部",
        "homepage_url": "https://www.sydwgkzp.cn/mnr/",
        "source_type": "mnr_recruitment",
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
        "config": {
            "api_base": "https://www.sydwgkzp.cn/mnr/api",
            "public_detail_url": "https://www.sydwgkzp.cn/mnr/index.html#/recruitmentDetails",
            "notice_type": 0,
            "require_major_match": True,
            "max_items": 10,
            "request_interval_seconds": 0,
        },
    }

    def post_json(url, payload):
        if url.endswith("GetAfficheList"):
            return FakeJsonResponse(
                {
                    "items": [
                        {
                            "ViewId": "notice-1",
                            "Title": "自然资源部所属单位公开招聘公告",
                            "FbDate": "2026-09-18T10:00:00",
                        }
                    ]
                }
            )
        if url.endswith("GetAfficheInfo"):
            return FakeJsonResponse(
                {
                    "Fbdw": "自然资源部所属单位",
                    "FbDate": "2026-09-18T10:00:00",
                    "BmjsDate": "2026-12-31T17:00:00",
                    "AnncCont": "<p>公开招聘工作人员。</p>",
                }
            )
        if url.endswith("GetPostSelectFyList"):
            return FakeJsonResponse(
                {
                    "items": [
                        {
                            "gwbm": "G-001",
                            "zpdw": "中国地质调查局测试中心",
                            "zpgw": "地质调查岗",
                            "zy": "地质工程、资源勘查工程",
                            "xwxlyq": "硕士研究生",
                            "gzdd": "新疆克拉玛依",
                            "gwyq": "地质调查和资源评价能力。",
                        },
                        {
                            "gwbm": "G-002",
                            "zpdw": "中国地质调查局测试中心",
                            "zpgw": "财务岗",
                            "zy": "会计学",
                            "xwxlyq": "本科",
                            "gzdd": "北京",
                        },
                    ]
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(collector, "_post_json", post_json)
    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].external_id == "notice-1:G-001"
    assert postings[0].location == "新疆克拉玛依"
    assert postings[0].deadline_date == "2026-12-31"
    assert "地质工程" in (postings[0].match_text or "")
    assert postings[0].source_url.endswith("?ViewId=notice-1")


def test_slb_public_search_keeps_only_detail_consistent_locations(
    tmp_path, monkeypatch
) -> None:
    """A generic role page must not be published under another country's label."""
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "slb-career",
        "name": "SLB Careers",
        "publisher": "SLB",
        "homepage_url": "https://careers.slb.com/job-listing",
        "source_type": "slb_coveo_search",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "config": {
            "allowed_hosts": ["careers.slb.com"],
            "api_allowed_hosts": ["platform.cloud.coveo.com"],
            "listing_url": "https://careers.slb.com/job-listing",
            "queries": ["geology"],
            "pipeline": "ATSJobsPipeline",
            "required_title_patterns": ["geolog"],
            "max_items": 10,
            "query_page_size": 10,
            "fetch_detail_pages": True,
            "require_detail_content": True,
            "minimum_detail_characters": 80,
            "require_location_evidence": True,
            "request_interval_seconds": 0,
        },
    }
    listing_url = "https://careers.slb.com/job-listing"
    incorrect_detail_url = (
        "https://careers.slb.com/jobdescription.aspx?id=Geologist&location='ChinaMulti-Location'"
    )
    correct_detail_url = "https://careers.slb.com/jobdescription.aspx?id=87273"
    responses = {
        listing_url: FakeResponse(
            text=(
                '<input id="organizationId" value="slb-org">'
                '<input id="accessToken" value="public-browser-token">'
                '<input id="searchHub" value="CoveoJobsHub">'
                '<input id="searchsource" value="ATS_Jobs_Source - Prod">'
            ),
            url=listing_url,
        ),
        incorrect_detail_url: FakeResponse(
            text=(
                "Job Name: Early Careers - Geologist City: Luanda, Angola "
                "Nationality: Open Job Summary: Interpret subsurface data and "
                "support reservoir evaluation. Requirements: Bachelor's degree in Geology."
            ),
            url=incorrect_detail_url,
        ),
        correct_detail_url: FakeResponse(
            text=(
                "Job Name: Geologist City: Tashkent, Uzbekistan Nationality: Uzbekistan "
                "Job Summary: Interpret seismic and petrophysical data for reservoir "
                "evaluation. Requirements: Bachelor's or Master's degree in Geology."
            ),
            url=correct_detail_url,
        ),
    }

    def get(url, _source):
        return responses[url]

    def post_json(url, payload, *, headers=None):
        assert url.startswith("https://platform.cloud.coveo.com/rest/search/v2?")
        assert payload["q"] == "geology"
        assert headers and headers["Authorization"] == "Bearer public-browser-token"
        return FakeJsonResponse(
            {
                "results": [
                    {
                        "title": "Early Careers - Geologist",
                        "clickUri": incorrect_detail_url,
                        "raw": {
                            "sysurihash": "generic-china-role",
                            "city": "Multi-Location",
                            "country": ["China"],
                            "date": 1789771794000,
                        },
                    },
                    {
                        "title": "Geologist",
                        "clickUri": correct_detail_url,
                        "raw": {
                            "sysurihash": "tashkent-geologist",
                            "city": "Tashkent",
                            "country": ["Uzbekistan"],
                            "jobposteddate": 1789543483000,
                        },
                    },
                ]
            }
        )

    monkeypatch.setattr(collector, "_get", get)
    monkeypatch.setattr(collector, "_post_json", post_json)

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].title == "Geologist"
    assert postings[0].location == "Tashkent, Uzbekistan"
    assert postings[0].published_date == "2026-09-16"
    assert "Master's degree" in (postings[0].match_text or "")


def test_slb_detail_requests_are_deduplicated_and_bounded(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "slb-career",
        "name": "SLB Careers",
        "publisher": "SLB",
        "homepage_url": "https://careers.slb.com/job-listing",
        "source_type": "slb_coveo_search",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "config": {
            "allowed_hosts": ["careers.slb.com"],
            "api_allowed_hosts": ["platform.cloud.coveo.com"],
            "listing_url": "https://careers.slb.com/job-listing",
            "search_api_url": "https://platform.cloud.coveo.com/rest/search/v2",
            "queries": ["geology", "geophysics"],
            "required_title_patterns": ["geolog"],
            "max_items": 5,
            "max_detail_candidates": 2,
            "query_page_size": 10,
            "minimum_detail_characters": 80,
            "require_location_evidence": True,
            "request_interval_seconds": 0,
        },
    }
    listing_url = "https://careers.slb.com/job-listing"
    detail_urls = [
        f"https://careers.slb.com/jobdescription.aspx?id={identifier}"
        for identifier in ("one", "two", "three")
    ]
    detail_requests: list[str] = []
    responses = {
        listing_url: FakeResponse(
            text=(
                '<input id="organizationId" value="slb-org">'
                '<input id="accessToken" value="public-browser-token">'
            ),
            url=listing_url,
        ),
        **{
            url: FakeResponse(
                text=(
                    f"Job Name: Geologist {index} City: Tashkent, Uzbekistan "
                    "Nationality: Uzbekistan Job Summary: Interpret seismic data "
                    "for reservoir evaluation. Requirements: Bachelor's degree in Geology."
                ),
                url=url,
            )
            for index, url in enumerate(detail_urls, start=1)
        },
    }

    def result(identifier: str) -> dict[str, object]:
        index = detail_urls.index(
            f"https://careers.slb.com/jobdescription.aspx?id={identifier}"
        ) + 1
        return {
            "title": f"Geologist {index}",
            "clickUri": detail_urls[index - 1],
            "raw": {
                "sysurihash": identifier,
                "city": "Tashkent",
                "country": ["Uzbekistan"],
            },
        }

    def get(url, _source):
        if url != listing_url:
            detail_requests.append(url)
        return responses[url]

    def post_json(_url, payload, *, headers=None):
        assert headers and headers["Authorization"] == "Bearer public-browser-token"
        if payload["q"] == "geology":
            return FakeJsonResponse({"results": [result("one")]})
        return FakeJsonResponse(
            {"results": [result("one"), result("two"), result("three")]}
        )

    monkeypatch.setattr(collector, "_get", get)
    monkeypatch.setattr(collector, "_post_json", post_json)

    postings = collector.collect(source)

    assert [posting.title for posting in postings] == ["Geologist 1", "Geologist 2"]
    assert detail_requests == detail_urls[:2]
