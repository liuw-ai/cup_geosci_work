# Phase 18：中石化官方 SPA 采集骨架

本阶段只处理中国石化官方校园招聘 SPA，不扩展事业编和公务员职位表。

## 本阶段已完成

- 新增 `sinopec_spa_rows` 来源类型。
- 新增浏览器捕获快照契约，校验官方域名、单位状态、岗位字段和岗位级证据。
- 新增中石化专用 `RawPosting` 转换器，进入现有标准化、专业匹配和发布审计流程。
- 新增 `sinopec-capture` CLI，输出 132 个单位、35 个候选单位、已捕获单位和岗位行统计。
- 用 2026-09-24 浏览器实际核验的胜利油田详情页建立首份快照，包含 3 条明确岗位级记录。

## 明确边界

当前快照只覆盖 1/132 个单位，来源保持 `enabled: false`。这不是完整接入，也不会增加学生端岗位。只有完成 132 个单位清单、35 个候选单位详情、分页、失败状态和回归证据后，才允许启用来源。

## 官方证据

- 单位列表：[中石化招聘信息](https://job.sinopec.com/#/school/recruitmentPositions)
- 详情页：[胜利油田 2027 年度校园招聘](https://job.sinopec.com/#/school/recruitEnterpriseDetail?deptId=BDA2ACEB-9C93-41B9-BFCB-887C71FB4C75)

## 验收命令

```text
python -m job_hub.cli sinopec-capture --path data/verified/sinopec-geoscience-20260924.json
python -m pytest tests/test_sinopec_spa.py -q
python -m pytest -q
```

## 下一步

1. 通过浏览器只读捕获保存 132 个单位分页清单。
2. 对“地质”筛选的 35 个候选单位逐一保存详情页和岗位分页证据。
3. 将每个单位写入 `success`、`scan_success_no_match`、`access_limited` 或 `manual_review_required`。
4. 完成完整清单后，将 `require_complete_manifest` 改为 `true`，再进行学生端发布验收。
