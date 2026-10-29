from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest

from job_hub.app import create_app
from job_hub.attachments import (
    OfficialAttachmentProcessor,
    build_attachment_field_evidence,
    infer_position_code,
)
from job_hub.db import Database
from job_hub.government_artifacts import register_government_artifacts

from conftest import make_settings, make_settings_with_artifact_path, source


def test_infer_position_code_requires_an_explicit_label() -> None:
    assert infer_position_code("岗位代码：2026113；专业：地质学") == "2026113"
    assert infer_position_code("岗位编码：2026120；专业：地质学") == "2026120"
    assert infer_position_code("职位编号 A-07，学历：硕士") == "A-07"
    assert infer_position_code("报名截止：2026-10-31；招聘人数：1") is None


def test_attachment_evidence_recovers_code_from_flattened_row() -> None:
    from job_hub.attachments import build_attachment_field_evidence

    evidence = build_attachment_field_evidence(
        title="专业技术岗位",
        major="地质学",
        degree="博士研究生",
        location="合肥市",
        artifact={"artifact_url": "https://dkj.ah.gov.cn/positions.xls"},
        row={"sheet_name": "岗位表", "row_number": 17},
        row_text="岗位代码：2026113；专业：地质学；学历：博士研究生",
    )

    assert evidence["职位代码"] == "2026113"


def test_position_code_alias_covers_government_job_encoding_header(tmp_path: Path) -> None:
    body = _position_xlsx_bytes(
        ["招聘单位", "岗位名称", "岗位编码", "招聘人数", "学历", "专 业"],
        ["测试地质院", "地质技术岗", "2026113", "1", "博士研究生", "地质学"],
    )
    _, database, artifact, processor = _registered_artifact(
        tmp_path,
        session=FakeSession(body),
        category="事业单位与人才引进",
    )

    processor.process(artifact["id"])

    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    assert candidate["field_evidence"]["职位代码"] == "2026113"


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
    sheet.append(["岗位名称", "岗位代码", "招聘单位", "工作地点", "招聘人数", "学历要求", "专业要求", "报名截止日期"])
    sheet.append(
        [
            "地质工程师",
            "G-001",
            "测试能源集团",
            "北京",
            "2",
            "硕士",
            "地质工程、地质学",
            "2026年12月31日",
        ]
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _position_xlsx_bytes(headers: list[str], values: list[str]) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "岗位表"
    sheet.append(headers)
    sheet.append(values)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _docx_position_bytes() -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("2026年公开招聘岗位表")
    table = document.add_table(rows=1, cols=6)
    for cell, value in zip(
        table.rows[0].cells,
        ["岗位代码", "岗位名称", "招聘单位", "工作地点", "学历要求", "专业要求"],
    ):
        cell.text = value
    row = table.add_row().cells
    for cell, value in zip(
        row,
        ["D-001", "地质工程师", "测试地质院", "武汉", "硕士", "地质工程、地质学"],
    ):
        cell.text = value
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _registered_artifact(
    tmp_path: Path,
    *,
    session: FakeSession,
    category: str = "能源、工程与地学拓展",
):
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
            "metadata": {"employer": "测试能源集团", "category": category},
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


def test_reparse_reuses_legacy_null_suffix_row_identity(tmp_path: Path) -> None:
    """Parser key normalization must not fork an existing attachment row."""
    settings, database, artifact, _ = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    legacy_identity = {
        "artifact_id": int(artifact["id"]),
        "sheet_name": "岗位表",
        "row_number": 2,
        "row_kind": "tabular",
        "row_key_suffix": None,
    }
    legacy_key = hashlib.sha256(
        json.dumps(legacy_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO source_artifact_rows (
                artifact_id, row_key, sheet_name, row_number, row_kind,
                cells_json, row_text, extraction_confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact["id"],
                legacy_key,
                "岗位表",
                2,
                "tabular",
                json.dumps({"岗位名称": "旧岗位"}, ensure_ascii=False),
                "旧岗位",
                "high",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        legacy_row_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": legacy_row_id,
            "source_id": "official-test-source",
            "official_page_url": artifact["parent_url"],
            "title": "旧岗位",
            "employer": "测试能源集团",
            "location": "北京",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "legacy",
            "description": "legacy",
            "extraction_confidence": "high",
            "field_evidence": {
                "artifact_url": artifact["artifact_url"],
                "专业范围": "地质工程",
                "学历要求": "硕士",
                "table_row": "岗位表!2",
            },
            "review_status": "official_content_verified",
        }
    )

    rows = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 2,
                "cells": {
                    "岗位名称": "新岗位",
                    "岗位代码": "G-002",
                    "招聘单位": "测试能源集团",
                    "工作地点": "北京",
                    "学历要求": "硕士",
                    "专业要求": "地质工程",
                },
                "row_text": "新岗位；G-002；地质工程；硕士；北京",
            }
        ],
    )

    assert len(rows) == 1
    assert rows[0]["id"] == legacy_row_id
    candidates = database.list_artifact_job_candidates(artifact_id=int(artifact["id"]))
    assert len(candidates) == 1
    assert candidates[0]["artifact_row_id"] == legacy_row_id
    assert candidates[0]["review_status"] == "official_content_verified"
    assert candidates[0]["row_cells"]["岗位代码"] == "G-002"


def test_reparse_preserves_stronger_legacy_candidate_over_canonical_duplicate(
    tmp_path: Path,
) -> None:
    """A legacy null-suffix row must not demote verified evidence on reparse."""
    settings, database, artifact, _ = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    row_payload = {
        "sheet_name": "岗位表",
        "row_number": 2,
        "cells": {
            "岗位名称": "地质技术岗",
            "岗位代码": "G-003",
            "招聘单位": "测试能源集团",
            "学历要求": "硕士",
            "专业要求": "地质工程",
        },
        "row_text": "地质技术岗；G-003；地质工程；硕士",
    }
    canonical = database.upsert_source_artifact_rows(int(artifact["id"]), [row_payload])[0]
    database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": canonical["id"],
            "source_id": "official-test-source",
            "official_page_url": artifact["parent_url"],
            "title": "地质技术岗",
            "employer": "测试能源集团",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "canonical",
            "description": "canonical",
            "field_evidence": {
                "artifact_url": artifact["artifact_url"],
                "专业范围": "地质工程",
                "学历要求": "硕士",
                "table_row": "岗位表!2",
            },
            "review_status": "needs_review",
        }
    )
    legacy_identity = {
        "artifact_id": int(artifact["id"]),
        "sheet_name": "岗位表",
        "row_number": 2,
        "row_kind": "tabular",
        "row_key_suffix": None,
    }
    legacy_key = hashlib.sha256(
        json.dumps(legacy_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO source_artifact_rows (
                artifact_id, row_key, sheet_name, row_number, row_kind,
                cells_json, row_text, extraction_confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact["id"],
                legacy_key,
                "岗位表",
                2,
                "tabular",
                json.dumps(row_payload["cells"], ensure_ascii=False),
                row_payload["row_text"],
                "high",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        legacy_row_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": legacy_row_id,
            "source_id": "official-test-source",
            "official_page_url": artifact["parent_url"],
            "title": "地质技术岗",
            "employer": "测试能源集团",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "legacy verified",
            "description": "legacy verified",
            "field_evidence": {
                "artifact_url": artifact["artifact_url"],
                "专业范围": "地质工程",
                "学历要求": "硕士",
                "table_row": "岗位表!2",
            },
            "review_status": "official_content_verified",
        }
    )

    rows = database.upsert_source_artifact_rows(int(artifact["id"]), [row_payload])

    candidates = database.list_artifact_job_candidates(artifact_id=int(artifact["id"]))
    verified = next(
        candidate
        for candidate in candidates
        if candidate["review_status"] == "official_content_verified"
    )
    rejected = next(candidate for candidate in candidates if candidate["review_status"] == "rejected")
    assert rows[0]["id"] == legacy_row_id
    assert verified["artifact_row_id"] == legacy_row_id
    assert rejected["artifact_row_id"] == canonical["id"]


def test_reconcile_reads_eligible_position_after_first_500_rows(tmp_path: Path) -> None:
    session = FakeSession(_xlsx_bytes())
    _, database, artifact, processor = _registered_artifact(tmp_path, session=session)
    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        content_sha256="a" * 64,
        storage_path="sha256/aa/" + "a" * 64 + ".xlsx",
        parser_version="attachments-test",
    )
    rows = [
        {
            "sheet_name": "岗位表",
            "row_number": index,
            "cells": {"说明": f"非岗位行 {index}"},
            "row_text": f"非岗位行 {index}",
        }
        for index in range(1, 501)
    ]
    rows.append(
        {
            "sheet_name": "岗位表",
            "row_number": 501,
            "cells": {
                "岗位名称": "地质工程师",
                "岗位代码": "G-501",
                "招聘单位": "测试能源集团",
                "工作地点": "北京市",
                "学历要求": "硕士研究生",
                "专业要求": "地质工程",
                "报名截止日期": "2099-12-31",
            },
            "row_text": "地质工程师；地质工程；硕士研究生；北京市",
        }
    )
    database.upsert_source_artifact_rows(int(artifact["id"]), rows)

    result = processor.reconcile_candidates(int(artifact["id"]))

    assert result.rows_extracted == 501
    assert result.candidates_created == 1
    candidates = list(database.iter_artifact_job_candidates(artifact_id=int(artifact["id"])))
    assert len(candidates) == 1
    assert candidates[0]["row_number"] == 501


def test_public_position_type_is_carried_into_row_evidence(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/notice/2",
            "artifact_url": "https://careers.example.edu.cn/files/public.xlsx",
            "artifact_kind": "position_table",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "metadata": {
                "employer": "测试事业单位",
                "category": "事业单位与人才引进",
                "position_type": "public_institution",
            },
        }
    )
    evidence = build_attachment_field_evidence(
        title="地质技术岗",
        major="工学大类",
        degree="硕士研究生",
        location="北京",
        artifact=artifact,
        row={"sheet_name": "岗位表", "row_number": 2},
        row_text="地质技术岗 工学大类 硕士研究生",
    )
    assert evidence["政府岗位类型"] == "public_institution"


def test_parallel_position_tables_do_not_overwrite_repeated_headers(tmp_path) -> None:
    """One official spreadsheet row may contain two independent job blocks."""
    body = _position_xlsx_bytes(
        [
            "岗位名称", "招聘单位", "工作地点", "学历要求", "专业要求",
            "岗位名称", "招聘单位", "工作地点", "学历要求", "专业要求",
        ],
        [
            "地质工程师", "湖南省地质院", "长沙", "硕士", "地质工程",
            "地球科学工程师", "湖南省地球物理院", "衡阳", "硕士", "地质学",
        ],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(body)
    )

    result = processor.process(artifact["id"])

    assert result.status == "extracted"
    assert result.rows_extracted == 2
    assert result.candidates_created == 2
    rows = database.list_source_artifact_rows(artifact["id"])
    assert {row["row_kind"] for row in rows} == {"tabular_parallel"}
    candidates = database.list_artifact_job_candidates(artifact_id=artifact["id"])
    assert {candidate["title"] for candidate in candidates} == {
        "地质工程师",
        "地球科学工程师",
    }
    assert {candidate["location"] for candidate in candidates} == {"长沙", "衡阳"}


def test_workbook_title_row_does_not_consume_real_position_header(tmp_path) -> None:
    """A merged notice title above the header must not hide early rows."""
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "岗位表"
    sheet.append(["湖南省地质院直属事业单位公开招聘岗位、计划数及要求一览表"])
    sheet.append(["序号", "招聘单位", "岗位名称", "招聘人数", "学历学位", "专业要求", "工作地点"])
    sheet.append(["1", "湖南省地质院", "地学科研岗", "2", "博士研究生", "地质学、地质资源与地质工程", "长沙"])
    body = BytesIO()
    workbook.save(body)
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(body.getvalue())
    )

    result = processor.process(artifact["id"])

    assert result.candidates_created == 1
    rows = database.list_source_artifact_rows(artifact["id"])
    assert rows[0]["row_number"] == 3
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    assert candidate["title"] == "地学科研岗"
    assert candidate["field_evidence"]["专业范围"] == "地质学、地质资源与地质工程"


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


def test_docx_position_table_is_extracted_row_by_row(tmp_path) -> None:
    body = _docx_position_bytes()
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    processor = OfficialAttachmentProcessor(settings, database, session=FakeSession(body))
    path = settings.managed_artifact_dir() / "positions.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    extracted = processor._extract_docx(path)

    assert extracted["metadata"]["parser"] == "python-docx"
    assert extracted["metadata"]["extraction_mode"] == "tables"
    assert extracted["metadata"]["table_count"] == 1
    assert extracted["rows"] == [
        {
            "sheet_name": "table-1",
            "row_number": 2,
            "row_kind": "tabular",
            "cells": {
                "岗位代码": "D-001",
                "岗位名称": "地质工程师",
                "招聘单位": "测试地质院",
                "工作地点": "武汉",
                "学历要求": "硕士",
                "专业要求": "地质工程、地质学",
            },
            "row_text": "D-001；地质工程师；测试地质院；武汉；硕士；地质工程、地质学",
            "extraction_confidence": "high",
        }
    ]


def test_docx_paragraph_fallback_remains_available(tmp_path) -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("地质工程师：硕士，地质工程，工作地点北京")
    body = BytesIO()
    document.save(body)
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    processor = OfficialAttachmentProcessor(settings, database, session=FakeSession(body.getvalue()))
    path = settings.managed_artifact_dir() / "notice.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.getvalue())

    extracted = processor._extract_docx(path)

    assert extracted["metadata"]["extraction_mode"] == "paragraphs"
    assert extracted["rows"][0]["row_kind"] == "text_table"


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


def test_discovery_reads_dates_from_script_rendered_notice_body(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    processor = OfficialAttachmentProcessor(settings, database, session=FakeSession(_xlsx_bytes()))

    discovered = processor.discover_from_page(
        "official-test-source",
        "https://careers.example.edu.cn/notice/2",
        html=(
            "<html><head><title>公开招聘</title></head><body>"
            "<script>var article='报名时间为2026年3月17日至3月24日';</script>"
            "<a href='/files/positions.xlsx'>岗位表</a></body></html>"
        ),
    )

    assert discovered[0]["metadata"]["deadline_date"] == "2026-03-24"


def test_discovery_accepts_allowlisted_extensionless_download_endpoint(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    extensionless_source = source() | {
        "config": {
            "minimum_relevance": 0,
            "allowed_hosts": ["careers.example.edu.cn"],
            "attachment_allowed_hosts": ["careers.example.edu.cn"],
            "attachment_url_patterns": [r"/front/download-"],
        }
    }
    database.upsert_source(extensionless_source)
    processor = OfficialAttachmentProcessor(
        settings, database, session=FakeSession(_xlsx_bytes())
    )

    discovered = processor.discover_from_page(
        "official-test-source",
        "https://careers.example.edu.cn/notice/1",
        html=(
            "<html><body>2026年公开招聘公告"
            "<a href='/front/download-abc123'>岗位和条件要求一览表</a>"
            "</body></html>"
        ),
    )

    assert len(discovered) == 1
    assert discovered[0]["artifact_url"].endswith("/front/download-abc123")


def test_position_conditions_title_is_classified_as_position_table() -> None:
    kind, intent = OfficialAttachmentProcessor._discovered_artifact_kind(
        "四川省地质局公开招聘工作人员岗位和条件要求一览表",
        "https://www.scpta.com.cn/front/download-abc123",
    )

    assert kind == "position_table"
    assert intent == "position_table_hint"


def test_extensionless_download_sniffs_xlsx_format(tmp_path) -> None:
    path = tmp_path / "download-token"
    path.write_bytes(_xlsx_bytes())

    assert OfficialAttachmentProcessor._sniff_suffix(path) == ".xlsx"


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
    assert public_job["field_evidence"]["招聘人数"] == "2"
    evidence = database.list_job_evidence(payload["job_id"])
    assert {item["evidence_type"] for item in evidence} == {
        "official_page",
        "attachment",
    }

    # Reprocessing a published row must remain idempotent. Older databases can
    # contain a null/invalid evidence JSON payload, so this exercises the
    # compatibility decoder used by the upsert path.
    reparsed = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": candidate["artifact_row_id"],
            "source_id": candidate["source_id"],
            "official_page_url": candidate["official_page_url"],
            "title": candidate["title"],
            "employer": candidate["employer"],
            "degree_levels": candidate["degree_levels"],
            "major_tags": candidate["major_tags"],
            "summary": candidate["summary"],
            "description": candidate["description"],
            "field_evidence": {**candidate["field_evidence"], "职位代码": "G-001"},
            "review_status": "needs_review",
        }
    )
    assert reparsed["review_status"] == "published"
    assert reparsed["published_job_id"] == payload["job_id"]


def test_status_update_preserves_official_major_and_degree_text(tmp_path) -> None:
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    original = candidate["field_evidence"]

    updated = database.update_artifact_job_candidate(
        candidate["id"],
        {
            "review_status": "official_content_verified",
            "review_note": "已核对官方附件岗位行。",
        },
    )

    assert updated["field_evidence"]["专业范围"] == original["专业范围"]
    assert updated["field_evidence"]["学历要求"] == original["学历要求"]


def test_attachment_row_requiring_experience_never_enters_student_review_queue(
    tmp_path,
) -> None:
    """A target major does not override a same-row experienced-hire condition."""
    session = FakeSession(
        _position_xlsx_bytes(
            [
                "岗位名称",
                "岗位代码",
                "招聘单位",
                "工作地点",
                "学历要求",
                "专业要求",
                "任职条件",
            ],
            [
                "地质工程师",
                "G-EXPERIENCE",
                "测试地质集团",
                "北京",
                "硕士研究生",
                "地质工程",
                "具有3年以上地质工作经验",
            ],
        )
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=session
    )

    result = processor.process(artifact["id"])

    assert result.candidates_created == 0
    assert database.list_artifact_job_candidates(artifact_id=artifact["id"]) == []


def test_government_candidate_location_can_be_verified_before_publication(tmp_path) -> None:
    """A notice-level address may complete an otherwise location-less table row."""
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]

    updated = database.update_artifact_job_candidate(
        candidate["id"],
        {
            "location": "北京市",
            "location_evidence": "官方公告正文地址：北京市海淀区学院路。",
            "review_status": "official_content_verified",
            "review_note": "已核对官方公告正文地址与附件岗位行；地点为公告载明的单位所在地。",
        },
    )
    assert updated["location"] == "北京市"
    assert updated["review_status"] == "official_content_verified"
    assert updated["field_evidence"]["工作地点依据"].startswith("官方公告正文地址")


def test_attachment_candidate_review_can_correct_extracted_fields_with_evidence(
    tmp_path,
) -> None:
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]

    updated = database.update_artifact_job_candidate(
        candidate["id"],
        {
            "title": "地质勘查岗",
            "employer": "测试地质集团",
            "location": "甘肃省兰州市",
            "degree_levels": ["博士"],
            "major_tags": ["地质资源与地质工程"],
            "field_evidence_updates": {
                "职位代码": "2026002",
                "招聘人数": "3",
                "工作地点依据": "官方岗位表第1页单位地址：甘肃省兰州市。",
            },
            "review_status": "official_content_verified",
            "review_note": "已逐项核对官方公告、岗位表原文和附件行，修正 PDF 断行字段。",
        },
    )

    assert updated["title"] == "地质勘查岗"
    assert updated["degree_levels"] == ["博士"]
    assert updated["field_evidence"]["招聘人数"] == "3"
    assert updated["field_evidence"]["专业范围"] == "地质资源与地质工程"


def test_site_attribution_is_rendered(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    body = app.test_client().get("/").get_data(as_text=True)
    assert "测试项目归属" in body


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


def test_manifest_manual_only_artifact_cannot_be_downloaded_by_direct_processor(tmp_path) -> None:
    session = FakeSession(_xlsx_bytes())
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    artifact = register_government_artifacts(
        database,
        {
            "as_of": "2026-09-30",
            "artifacts": [
                {
                    "id": "manual-government-artifact",
                    "source_id": "official-test-source",
                    "province": "全国",
                    "position_type": "public_institution",
                    "notice_url": "https://careers.example.edu.cn/notice/1",
                    "attachment_url": "https://careers.example.edu.cn/files/positions.xlsx",
                    "artifact_kind": "position_table",
                    "deadline_date": "2099-12-31",
                    "deadline_policy": "fixed_date",
                    "observed_on": "2026-09-30",
                    "status": "manual_verified",
                    "note": "manual review only",
                }
            ],
        },
    )[0]
    processor = OfficialAttachmentProcessor(settings, database, session=session)

    result = processor.process(int(artifact["id"]), force_download=True)

    assert result.status == "skipped"
    assert "manual evidence confirmation" in result.detail
    assert session.calls == []
    stored = database.get_source_artifact(int(artifact["id"]))
    assert stored is not None
    assert stored["extraction_status"] == "skipped"
    assert "manual evidence confirmation" in stored["metadata"]["processing_skip_reason"]


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


def test_discovery_classifies_application_forms_outside_position_tables(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    processor = OfficialAttachmentProcessor(settings, database, session=FakeSession(_xlsx_bytes()))

    discovered = processor.discover_from_page(
        "official-test-source",
        "https://careers.example.edu.cn/notice/1",
        html="<a href='/files/application.xlsx'>附件：公开招聘报名表</a>",
    )

    assert discovered[0]["artifact_kind"] == "application_material"
    assert discovered[0]["metadata"]["attachment_intent"].startswith(
        "non_position_attachment:报名表"
    )


def test_non_geoscience_position_rows_do_not_enter_private_review_queue(tmp_path) -> None:
    body = _position_xlsx_bytes(
        ["岗位名称", "招聘单位", "工作地点", "学历要求", "专业要求"],
        ["生物信息分析师", "测试医院", "北京市", "本科及以上", "生物信息学"],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(body)
    )

    result = processor.process(artifact["id"])

    assert result.status == "extracted"
    assert result.candidates_created == 0
    assert database.list_artifact_job_candidates(artifact_id=artifact["id"]) == []


def test_public_government_similar_geoscience_row_enters_review_queue(tmp_path) -> None:
    """Public tables retain adjacent geoscience rows for explicit tiering."""
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/notice/public-similar",
            "artifact_url": "https://careers.example.edu.cn/files/public-similar.xlsx",
            "artifact_kind": "position_table",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "metadata": {
                "employer": "测试事业单位",
                "category": "事业单位与人才引进",
                "position_type": "public_institution",
            },
        }
    )
    processor = OfficialAttachmentProcessor(
        settings,
        database,
        session=FakeSession(
            _position_xlsx_bytes(
                ["岗位名称", "岗位代码", "招聘单位", "工作地点", "学历要求", "专业要求"],
                ["地球化学研究岗", "P-001", "测试事业单位", "北京", "博士研究生", "地球化学"],
            )
        ),
    )

    result = processor.process(artifact["id"])

    assert result.candidates_created == 1
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    assert candidate["review_status"] == "needs_review"
    assert "地球化学" in candidate["field_evidence"]["专业范围"]


def test_unrestricted_enterprise_position_row_stays_private(tmp_path) -> None:
    body = _position_xlsx_bytes(
        ["岗位名称", "岗位代码", "招聘单位", "工作地点", "学历要求", "专业要求"],
        ["综合管理岗", "A-001", "测试事业单位", "北京市", "本科及以上", "不限专业"],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(body)
    )

    result = processor.process(artifact["id"])
    candidates = database.list_artifact_job_candidates(artifact_id=artifact["id"])

    assert result.candidates_created == 0
    assert candidates == []


def test_combined_enterprise_condition_columns_are_mapped_without_relaxing_gates(tmp_path) -> None:
    body = _position_xlsx_bytes(
        ["序号", "人才需求单位", "需求岗位", "需求理由", "岗位职责", "岗位条件", "备注"],
        [
            "1",
            "山西潞安中煤资源勘查开发有限公司",
            "施工技术岗",
            "业务需要",
            "协助完成地质普查、勘探项目设计和报告整理",
            "地质工程、资源勘查工程专业；本科及以上学历",
            "1",
        ],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(body)
    )

    result = processor.process(artifact["id"])

    assert result.candidates_created == 1
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    assert candidate["title"] == "施工技术岗"
    assert candidate["employer"] == "山西潞安中煤资源勘查开发有限公司"
    assert candidate["degree_levels"] == ["本科"]
    assert "资源勘查" in candidate["major_tags"]
    assert candidate["field_evidence"]["招聘人数"] == "1"
    assert candidate["field_evidence"]["招聘人数原字段"] == "备注"


@pytest.mark.parametrize(
    ("headers", "values", "expected_major", "category"),
    [
        (
            ["序号", "单位名称", "岗位名称", "招聘人数", "学历学位", "专业及代码"],
            ["1", "中国地质科学院", "深地大数据科研岗A", "1", "硕士研究生", "地质资源与地质工程（0818）"],
            "地质资源与地质工程",
            "事业单位与人才引进",
        ),
        (
            ["岗位代码", "招聘单位名称", "岗位名称", "招聘计划人数", "学历", "岗位所需专业"],
            ["420001", "湖北省自然资源厅", "矿产资源管理岗", "2", "本科及以上", "地质学类、地质资源与地质工程类"],
            "地质资源与地质工程",
            "事业单位与人才引进",
        ),
        (
            ["岗位代码", "招聘单位名称", "岗位名称", "招聘\n计划数", "学历", "岗位所需专业"],
            ["420002", "湖南省地质院", "地质技术岗", "1", "硕士研究生", "0709 地质学"],
            "地质学",
            "事业单位与人才引进",
        ),
        (
            ["招聘单位", "岗位名称", "岗位编码", "招聘人数", "学历或学位", "专业条件要求"],
            ["四川省第一地质大队", "地质技术岗", "200004001002", "1", "研究生学历及以上相应学位", "地质工程专业、岩土工程专业"],
            "地质工程",
            "事业单位与人才引进",
        ),
    ],
)
def test_official_position_table_aliases_cover_government_field_labels(
    tmp_path, headers, values, expected_major, category
) -> None:
    """Common government-table labels must reach the private review queue."""
    settings, database, artifact, processor = _registered_artifact(
        tmp_path,
        session=FakeSession(_position_xlsx_bytes(headers, values)),
        category=category,
    )

    result = processor.process(artifact["id"])
    candidates = database.list_artifact_job_candidates(artifact_id=artifact["id"])

    assert result.candidates_created == 1
    assert len(candidates) == 1
    assert candidates[0]["review_status"] == "needs_review"
    assert expected_major in candidates[0]["field_evidence"]["专业范围"]
    assert candidates[0]["field_evidence"]["招聘人数"] == values[3]


def test_ningxia_style_position_table_maps_required_major_without_city(tmp_path) -> None:
    """A real government XLS may use \"所需专业\" and omit the city field."""
    body = _position_xlsx_bytes(
        [
            "招聘主管部门\n（联系电话）",
            "招聘单位",
            "岗位\n名称",
            "岗位\n代码",
            "招聘  人数",
            "学历",
            "学位",
            "所需专业",
            "与岗位相关\n的其他要求",
        ],
        [
            "自治区地质局（0951-2036329）",
            "自治区煤炭地质局",
            "地质矿产岗",
            "001",
            "1",
            "研究生",
            "博士",
            "矿产普查与勘探、地质工程、地质学",
            "本科或硕士研究生任一学习阶段所学专业须为地质相关专业",
        ],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(body)
    )

    result = processor.process(artifact["id"])
    candidates = database.list_artifact_job_candidates(artifact_id=artifact["id"])

    assert result.candidates_created == 1
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["title"] == "地质矿产岗"
    assert candidate["employer"] == "自治区煤炭地质局"
    assert candidate["location"] is None
    assert candidate["degree_levels"] == ["博士"]
    assert "地质工程" in candidate["major_tags"]
    assert candidate["field_evidence"]["职位代码"] == "001"
    assert candidate["field_evidence"]["招聘人数"] == "1"


def test_official_position_row_without_degree_or_location_enters_review_queue(tmp_path) -> None:
    """Missing optional fields must not discard a row with official major evidence."""
    body = _position_xlsx_bytes(
        ["岗位代码", "招聘单位", "岗位名称", "招聘人数", "专业要求"],
        ["M-001", "测试地质院", "地质技术岗", "1", "地质工程、地质学"],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path,
        session=FakeSession(body),
        category="事业单位与人才引进",
    )

    result = processor.process(artifact["id"])

    assert result.candidates_created == 1
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    assert candidate["location"] is None
    assert candidate["degree_levels"] == []
    assert not candidate["field_evidence"].get("学历要求")


def test_spaced_government_headers_are_recognized_and_restore_only_shape_false_negative(
    tmp_path,
) -> None:
    """A real XLS label such as ``专 业`` must not be rejected before parsing."""
    body = _position_xlsx_bytes(
        ["岗位名称", "专 业", "学历", "学位", "岗位代码", "招聘单位", "拟聘人数"],
        [
            "专业技术岗位",
            "地质学、地质资源与地质工程、地球物理学",
            "研究生",
            "博士",
            "2026113",
            "安徽工业经济职业技术学院",
            "1",
        ],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path,
        session=FakeSession(body),
        category="事业单位与人才引进",
    )

    first = processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    database.reject_unreviewed_artifact_candidates(
        artifact["id"],
        reason=(
            "Phase 62 附件用途识别：未发现同时包含岗位/职位和专业字段的"
            "职位表结构；原始附件与行证据已保留。"
        ),
    )

    reconciled = processor.reconcile_candidates(artifact["id"])
    restored = database.get_artifact_job_candidate(candidate["id"])

    assert first.candidates_created == 1
    assert reconciled.candidates_created == 1
    assert restored["review_status"] == "needs_review"
    assert restored["review_note"] == ""
    assert restored["location"] is None
    assert restored["degree_levels"] == ["博士"]
    assert "地质资源与地质工程" in restored["major_tags"]


def test_spaced_government_headers_do_not_restore_other_rejections(tmp_path) -> None:
    body = _position_xlsx_bytes(
        ["岗位名称", "专 业", "岗位代码", "招聘单位"],
        ["地质技术岗", "地质工程", "A-001", "测试地质院"],
    )
    settings, database, artifact, processor = _registered_artifact(
        tmp_path,
        session=FakeSession(body),
        category="事业单位与人才引进",
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    database.reject_unreviewed_artifact_candidates(
        artifact["id"],
        reason="人工复核：该岗位明确要求五年行业经验，不适合应届学生。",
    )

    processor.reconcile_candidates(artifact["id"])

    retained = database.get_artifact_job_candidate(candidate["id"])
    assert retained["review_status"] == "rejected"
    assert "人工复核" in retained["review_note"]


def test_reconcile_rejects_old_candidates_from_an_application_form(tmp_path) -> None:
    settings, database, artifact, processor = _registered_artifact(
        tmp_path, session=FakeSession(_xlsx_bytes())
    )
    processor.process(artifact["id"])
    candidate = database.list_artifact_job_candidates(artifact_id=artifact["id"])[0]
    database.update_source_artifact_processing(
        artifact["id"],
        extraction_status="extracted",
        metadata_updates={
            "display_name": "公开招聘报名表",
            "last_error": "旧容器缺少候选拒绝方法",
            "last_error_at": "2026-09-28T10:01:11Z",
        },
    )

    result = processor.reconcile_candidates(artifact["id"])
    refreshed = database.get_artifact_job_candidate(candidate["id"])
    stored = database.get_source_artifact(artifact["id"])

    assert result.candidates_created == 0
    assert result.candidates_rejected == 1
    assert refreshed["review_status"] == "rejected"
    assert "Phase 62 附件用途识别" in refreshed["review_note"]
    assert stored["metadata"]["candidate_queue_status"] == "rejected_non_position_attachment"
    assert stored["metadata"]["last_error"] is None
    assert stored["metadata"]["last_error_at"] is None
