from job_hub.discovery_scan import extract_discovery_evidence


def test_discovery_evidence_extracts_plain_text_official_url_as_private_candidate() -> None:
    body = (
        "<html><body>官方原文："
        "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=42"
        "，培训入口：https://unknown.example/course"
        "</body></html>"
    )

    evidence = extract_discovery_evidence(
        body,
        "https://sydw.huatu.com/2026/1010/42.html",
        allowed_hosts={"sydw.huatu.com"},
        official_hosts={"zhaopin.cnpc.com.cn"},
    )

    assert evidence["parser_version"] == "discovery-evidence-v2"
    assert [item["url"] for item in evidence["official_link_candidates"]] == [
        "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=42"
    ]


def test_discovery_evidence_ignores_footer_registration_links() -> None:
    evidence = extract_discovery_evidence(
        "<footer>ICP备案：<a href='https://beian.miit.gov.cn'>备案号</a> "
        "公安备案：https://beian.mps.gov.cn/ "
        "域名备案：https://www.beian.gov.cn/portal/registerSystemInfo</footer>",
        "https://sydw.huatu.com/2026/1010/42.html",
        allowed_hosts={"sydw.huatu.com"},
    )

    assert evidence["official_link_candidates"] == []


def test_recruitment_url_filter_rejects_filing_subdomains() -> None:
    from job_hub.discovery import is_recruitment_official_url

    assert not is_recruitment_official_url(
        "https://www.beian.gov.cn/portal/registerSystemInfo"
    )


def test_discovery_evidence_trims_wrapper_text_after_homepage_url() -> None:
    evidence = extract_discovery_evidence(
        "官网：https://wqb.xa.gov.cn/)打印准考证",
        "https://sydw.huatu.com/2026/1010/1.html",
        allowed_hosts={"sydw.huatu.com"},
    )
    assert evidence["official_link_candidates"][0]["url"] == "https://wqb.xa.gov.cn/"


def test_discovery_evidence_decodes_json_escaped_and_percent_encoded_urls() -> None:
    body = (
        '<script>window.__DATA__ = '
        '{"target":"https:\\/\\/zhaopin.cnpc.com.cn\\/web\\/recruitInfoshow.html?'
        'id=43", "encoded":"https%3A%2F%2Fjob.sinopec.com%2Fjobs%2F44"}'
        '</script>'
    )

    evidence = extract_discovery_evidence(
        body,
        "https://sydw.huatu.com/2026/1010/43.html",
        allowed_hosts={"sydw.huatu.com"},
        official_hosts={"zhaopin.cnpc.com.cn", "job.sinopec.com"},
    )

    urls = {item["url"] for item in evidence["official_link_candidates"]}
    assert "https://zhaopin.cnpc.com.cn/web/recruitInfoshow.html?id=43" in urls
    assert "https://job.sinopec.com/jobs/44" in urls


def test_discovery_evidence_unwraps_known_transparent_redirect_wrapper() -> None:
    body = (
        '<a href="https://link.wtturl.cn/?target=https%3A%2F%2Fjob.sinopec.com%2Fjobs%2F45">'
        "官网岗位详情</a>"
    )

    evidence = extract_discovery_evidence(
        body,
        "https://www.offcn.com/gqzp/2026/1022/258317.html",
        allowed_hosts={"www.offcn.com"},
        official_hosts={"job.sinopec.com"},
    )

    assert [item["url"] for item in evidence["official_link_candidates"]] == [
        "https://job.sinopec.com/jobs/45"
    ]
