# Phase 80 质量报告

## 自动化验证

- `python -m pytest tests/test_attachments.py -q`：`21 passed`
- `python -m pytest -q`：`379 passed`

覆盖内容：

- Word 岗位表的表头、逐行字段、行号和原始文本提取。
- 重复表头不覆盖字段。
- 无表格 Word 公告的段落兜底。
- 原有 PDF、XLS/XLSX、OCR、候选发布和证据门禁回归。

## 真实性边界

服务器重新扫描后，只有出现当前报名期且明确匹配地学院专业/学历的 Word 岗位，才会增加学生端数量；解析成功但无匹配、已截止或访问受限分别记录，不混为“无岗位”。
