from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from job_hub.app import create_app
from job_hub.attachments import OfficialAttachmentProcessor
from job_hub.db import Database

from conftest import make_settings, make_settings_with_artifact_path, source


def test_relative_artifact_storage_is_resolved_below_app_data(tmp_path: Path) -> None:
    settings = make_settings_with_artifact_path(
        tmp_path, Path("runtime") / "official-attachments"
    )

    assert settings.managed_artifact_dir() == (
        tmp_path / "runtime" / "official-attachments"
    ).resolve()


def test_absolute_artifact_storage_must_remain_below_app_data(tmp_path: Path) -> None:
    settings = make_settings_with_artifact_path(tmp_path, tmp_path.parent / "outside")

    with pytest.raises(ValueError, match="inside APP_DATA_DIR"):
        settings.managed_artifact_dir()


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        body: bytes = b"",
        status_code: int = 200,
        content_type: str | None = None,
        text: str = "",
    ) -> None:
        self.url = url
        self.body = body
        self.status_code = status_code
        self.headers = {}
        if content_type:
            self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))
        self.text = text

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def iter_content(self, chunk_size: int = 65536):
        yield self.body

    def close(self) -> None:
        return None


class FakeSession:
    def __init__(self, body: bytes, *, robots: str = "") -> None:
        self.body = body
        self.robots = robots
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(url=url, text=self.robots)
        return FakeResponse(
            url=url,
            body=self.body,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def _xlsx_bytes() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "岗位表"
    sheet.append(["岗位名称", "岗位代码", "招聘单位", "工作地点", "学历要求", "专业要求", "报名截止日期"])
    sheet.append(
        [
            "地质工程师",
            "G-001",
            "测试能源集团",
            "北京",
            "硕士",
            "地质工程、地质学",
            "2026年12月31日",
        ]
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _registered_artifact(tmp_path: Path, *, session: FakeSession):
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/notice/1",
            "artifact_url": "https://careers.example.edu.cn/files/positions.xlsx",
            "artifact_kind": "position_table",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "metadata": {"employer": "测试能源集团", "category": "能源、工程与地学拓展"},
        }
    )
    processor = OfficialAttachmentProcessor(settings, database, session=session)
    return settings, database, artifact, processor


def test_excel_attachment_is_hashed_extracted_and_idempotent(tmp_path) -> None:
    session = FakeSession(_xlsx_bytes())
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=session
    )

    first = processor.process(artifact["id"])
    assert first.status == "extracted"
    assert first.rows_extracted == 1
    assert first.candidates_created == 1
    stored = database.get_source_artifact(artifact["id"])
    assert stored["extraction_status"] == "extracted"
    assert len(stored["content_sha256"]) == 64
    assert stored["storage_path"].startswith("sha256/")
    assert (settings.managed_artifact_dir() / stored["storage_path"]).is_file()

    rows = database.list_source_artifact_rows(artifact["id"])
    candidates = database.list_artifact_job_candidates(artifact_id=artifact["id"])
    assert len(rows) == len(candidates) == 1
    assert candidates[0]["review_status"] == "needs_review"
    assert candidates[0]["major_tags"]
    assert candidates[0]["row_sheet_name"] == "岗位表"
    assert candidates[0]["field_evidence"]["evidence_scope"] == "official_attachment_row"
    assert candidates[0]["field_evidence"]["岗位"] == "地质工程师"
    assert "地质工程" in candidates[0]["field_evidence"]["专业范围"]
    assert candidates[0]["field_evidence"]["学历要求"] == "硕士"
    assert candidates[0]["field_evidence"]["职位代码"] == "G-001"

    second = processor.process(artifact["id"])
    assert second.rows_extracted == 1
    assert second.candidates_created == 1
    assert len(database.list_source_artifact_rows(artifact["id"])) == 1
    assert len(database.list_artifact_job_candidates(artifact_id=artifact["id"])) == 1


def test_mislabelled_xls_with_ooxml_content_uses_excel_parser(tmp_path) -> None:
    """Government portals sometimes serve an XLSX workbook under a .xls name."""
    settings, database, _, processor = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    path = settings.managed_artifact_dir() / "mislabelled.xls"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_xlsx_bytes())

    extracted = processor._extract_legacy_workbook(path)

    assert extracted["metadata"]["parser"] == "openpyxl (mislabelled .xls)"
    assert extracted["metadata"]["extraction_mode"] == "workbook_rows"
    assert len(extracted["rows"]) == 1
    assert extracted["rows"][0]["cells"]["岗位名称"] == "地质工程师"


def test_discovery_carries_official_notice_dates_into_xls_artifact(tmp_path) -> None:
    session = FakeSession(_xlsx_bytes())
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    processor = OfficialAttachmentProcessor(settings, database, session=session)

    discovered = processor.discover_from_page(
        "official-test-source",
        "https://careers.example.edu.cn/notice/1",
        html=(
            "<html><head><title>2026年公开招聘公告</title></head><body>"
            "发布时间：2026-09-01 报名截止日期：2026年10月31日"
            "<a href='/files/positions.xls'>岗位表</a></body></html>"
        ),
    )

    assert len(discovered) == 1
    assert discovered[0]["metadata"]["published_date"] == "2026-09-01"
    assert discovered[0]["metadata"]["deadline_date"] == "2026-10-31"
    assert discovered[0]["metadata"]["official_notice_title"] == "2026年公开招聘公告"


def test_attachment_candidate_requires_review_then_publishes_with_evidence(tmp_path) -> None:
    session = FakeSession(_xlsx_bytes())
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=session
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]

    with pytest.raises(ValueError, match="review_note"):
        database.update_artifact_job_candidate(
            candidate["id"], {"review_status": "official_content_verified"}
        )
    verified = database.update_artifact_job_candidate(
        candidate["id"],
        {
            "review_status": "official_content_verified",
            "review_note": "已逐项核对公告正文、职位表、学历和专业要求。",
        },
    )
    assert verified["review_status"] == "official_content_verified"

    app = create_app(settings)
    response = app.test_client().post(
        f"/api/admin/artifact-candidates/{candidate['id']}/publish",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["candidate"]["review_status"] == "published"
    public_job = database.find_public_job(payload["job_id"])
    assert public_job is not None
    assert public_job["publication_status"] == "student_eligible"
    assert public_job["field_evidence"]["专业范围"] == "地质工程、地质学"
    evidence = database.list_job_evidence(payload["job_id"])
    assert {item["evidence_type"] for item in evidence} == {
        "official_page",
        "attachment",
    }


def test_attachment_publication_rolls_back_job_when_evidence_fails(
    tmp_path, monkeypatch
) -> None:
    session = FakeSession(_xlsx_bytes())
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=session
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    database.update_artifact_job_candidate(
        candidate["id"],
        {
            "review_status": "official_content_verified",
            "review_note": "已核对官方公告和附件职位表。",
        },
    )

    app = create_app(settings)
    app_database = app.extensions["database"]

    def fail_evidence(*args, **kwargs):
        raise ValueError("simulated evidence failure")

    monkeypatch.setattr(app_database, "add_job_evidence", fail_evidence)
    response = app.test_client().post(
        f"/api/admin/artifact-candidates/{candidate['id']}/publish",
        headers={"X-Admin-Token": "test-admin-token"},
    )

    assert response.status_code == 409
    assert database.list_artifact_job_candidates(
        artifact_id=artifact["id"], review_status="official_content_verified"
    )
    assert database.find_job(1) is None


def test_attachment_download_rejects_cross_host_and_robots_denied(tmp_path) -> None:
    session = FakeSession(_xlsx_bytes())
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=session
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE source_artifacts SET artifact_url = ? WHERE id = ?",
            ("https://files.other.example/jobs.xlsx", artifact["id"]),
        )
    result = processor.process(artifact["id"])
    assert result.status == "skipped"
    assert "official source allowlist" in result.detail

    (tmp_path / "denied").mkdir()
    settings2, database2, artifact2, denied = _registered_artifact(
        tmp_path / "denied", session=FakeSession(_xlsx_bytes(), robots="User-agent: *\nDisallow: /files/\n")
    )
    result = denied.process(artifact2["id"])
    assert result.status == "skipped"
    assert "robots.txt" in result.detail
    assert database2.get_source_artifact(artifact2["id"])["extraction_status"] == "skipped"


def test_discovery_registers_only_official_file_links_without_downloading(tmp_path) -> None:
    session = FakeSession(_xlsx_bytes())
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    processor = OfficialAttachmentProcessor(settings, database, session=session)
    html = """
    <html><body>
      <a href="/files/positions.xlsx">2026 岗位表</a>
      <a href="https://external.example/jobs.pdf">外部转载</a>
      <a href="/notice/related">普通正文链接</a>
    </body></html>
    """
    discovered = processor.discover_from_page(
        "official-test-source",
        "https://careers.example.edu.cn/notice/1",
        html=html,
    )
    assert len(discovered) == 1
    assert discovered[0]["artifact_kind"] == "position_table"
    assert discovered[0]["extraction_status"] == "registered"
    assert not (settings.managed_artifact_dir() / "sha256").exists()
