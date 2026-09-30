# Phase 92 质量报告

## 数据门禁

| 项目 | 结果 |
| --- | --- |
| 官方公告 | 中国地震局 `5855536` 公告页 |
| 官方附件 | `2026092316044245325.xlsx` |
| 已核验附件 SHA-256 | `646e153605e06596c21601695b1072ba1a74a8f24ec18321fd54b3d184589729` |
| CEA 明确匹配职位行 | 91 |
| CEA 计划名额 | 112 |
| 报名窗口 | 2026-10-10 08:00 至 2026-10-26 18:00 |
| 当前在招统计 | 不包含 CEA，直至 2026-10-10 |

## 关键边界

- CEA 附件的自动下载被 `robots.txt` 拒绝。该状态不是“无岗位”，也不能通过改 User-Agent、代理或浏览器绕过。
- 24 条只有相邻专业的候选不进入公开岗位库，保留为后续专业范围变更时可重新审查的来源证据，而不是数量指标。
- 数据库同步前，`source_opening_dates` 会阻止 CEA 职位在报名开始日前公开；截止日期后会由既有清退门禁撤回。

## 本地回归

`PYTHONPATH=. pytest -q tests/test_government_positions.py tests/test_sources.py tests/test_government_artifacts.py`：52 passed。

`python -m compileall -q job_hub tests`：通过。
