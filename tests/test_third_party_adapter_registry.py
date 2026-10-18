from job_hub.third_party_tables import discover_table_links, load_table_adapters


def test_all_batch_sources_have_explicit_table_adapters():
    adapters = load_table_adapters()
    assert set(adapters) == {
        "offcn-discovery",
        "fenbi-discovery",
        "huatu-discovery",
        "gongkaoleida-discovery",
        "gongkaozixun-discovery",
    }
    assert all(item["manual_review_required"] for item in adapters.values())


def test_huatu_adapter_covers_state_owned_subsite_without_changing_publication_gate():
    adapter = load_table_adapters()["huatu-discovery"]
    assert "www.sydw8.com" in adapter["allowed_hosts"]
    assert "https://www.sydw8.com/guoqi/zhaokao/" in adapter["scan_urls"]
    assert adapter["official_url_required"] is True


def test_table_discovery_keeps_third_party_url_as_candidate_only():
    html = '<a href="/2026/jobs.xlsx">2026 国考职位表下载</a><a href="https://gov.example/jobs.xlsx">外部</a>'
    rows = discover_table_links(
        html,
        page_url="https://www.offcn.com/guide/",
        source_id="offcn-discovery",
    )
    assert len(rows) == 1
    assert rows[0]["table_url"].endswith("/2026/jobs.xlsx")
    assert rows[0]["official_url"] is None
    assert rows[0]["publication_status"] == "candidate_only"
