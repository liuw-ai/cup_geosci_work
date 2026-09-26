from __future__ import annotations

from job_hub.attachments import OfficialAttachmentProcessor


def test_position_pdf_rows_keep_code_and_multiline_geoscience_fields_together() -> None:
    pages = [
        """
岗位代码    招聘单位                    岗位名称      招聘人数    学历       专业
202602104   甘肃省地矿局第二地质矿产勘查院  地质技术人员  1           本科及以上  资源勘查工程、矿产普查与勘探
            （兰州）                  负责野外地质调查和矿产勘查工作
202602201   甘肃省地矿局第三地质矿产勘查院  地质技术人员  2           本科        资源勘查工程
        """,
    ]

    rows = OfficialAttachmentProcessor._rows_from_pdf_pages(pages)

    position_rows = [row for row in rows if "职位代码" in row["cells"]]
    assert [row["cells"]["职位代码"] for row in position_rows] == [
        "202602104",
        "202602201",
    ]
    first = position_rows[0]
    assert first["cells"]["岗位名称"] == "地质技术人员"
    assert first["cells"]["学历要求"] == "本科及以上"
    assert "资源勘查工程" in first["cells"]["专业要求"]
    assert "负责野外地质调查" in first["row_text"]


def test_position_pdf_code_accepts_compact_codes_and_ignores_headers() -> None:
    pages = [
        """
岗位代码    岗位名称    学历    专业
202602103 地质技术人员 本科 环境地质工程
岗位代码    岗位名称    学历    专业
202602102    地质技术人员    本科    人文地理与城乡规划
        """,
    ]

    rows = OfficialAttachmentProcessor._rows_from_pdf_pages(pages)

    assert [row["cells"]["职位代码"] for row in rows if "职位代码" in row["cells"]] == [
        "202602103",
        "202602102",
    ]


def test_position_pdf_code_does_not_join_serial_number_to_code() -> None:
    assert OfficialAttachmentProcessor._pdf_position_code(
        "1  202602101  专业技术岗位"
    ) == "202602101"

    rows = OfficialAttachmentProcessor._rows_from_pdf_pages(
        ["1  202602101  地质技术人\n员  本科  资源勘查工程"]
    )
    assert rows[0]["cells"]["职位代码"] == "202602101"
    assert rows[0]["cells"]["岗位名称"] == "地质技术人员"
