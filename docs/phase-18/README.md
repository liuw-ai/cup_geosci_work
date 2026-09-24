# Phase 18：中石化官方 SPA 采集骨架

本阶段只处理中国石化官方校园招聘 SPA，不扩展事业编和公务员职位表。

## 本阶段已完成

- 新增 `sinopec_spa_rows` 来源类型。
- 新增浏览器捕获快照契约，校验官方域名、单位状态、岗位字段和岗位级证据。
- 新增中石化专用 `RawPosting` 转换器，进入现有标准化、专业匹配和发布审计流程。
- 新增 `sinopec-capture` CLI，输出 132 个单位、35 个候选单位、已捕获单位和岗位行统计。
- 用 2026-09-24 浏览器实际核验的官方 SPA 建立完整快照：132 个单位分页清单、35 个“地质”候选单位详情页、348 条岗位行。

## 明确边界

当前快照已经覆盖 132/132 个单位清单和 35/35 个候选单位详情。348 条岗位已进入现有专业、学历和证据门禁：69 条明确匹配、98 条待核验、181 条专业不匹配；来源仍保持 `enabled: false`，管理员审阅通过后才允许发布明确匹配岗位。

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

1. 管理员复核 69 条明确匹配岗位的专业与学历证据，确认非地学岗位不会进入学生端。
2. 对 98 条待核验岗位补充岗位级专业原文，或保持不发布。
3. 验收通过后才将 `enabled` 改为 `true`，再运行日报和 100 人模拟。
