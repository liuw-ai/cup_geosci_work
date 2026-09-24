# Phase 18 迁移说明

## 数据与配置

- `data/sources.json` 的 `sinopec-career` 改为 `sinopec_spa_rows`。
- 新增并更新 `data/verified/sinopec-geoscience-20260924.json`，包含 132 个单位、35 个候选单位和 348 条岗位行。
- 来源仍为停用状态；不会改变现有学生端岗位数据。管理员审计通过后才允许启用。
- 本轮门禁修复将 `official_sinopec_detail_snapshot` 注册为合法岗位级证据范围；不涉及数据库结构迁移。
- 对 2026-09-24 快照的审计结果为：348 条岗位中 69 条明确匹配、98 条待核验、181 条不匹配。来源仍为停用状态。

## 代码

- `job_hub/sinopec.py`：捕获文件契约、单位状态和摘要统计。
- `job_hub/sources.py`：将捕获岗位转换成统一 `RawPosting`，并允许显式上限 500（最大 1000）读取完整快照。
- `job_hub/profiles.py`：将中石化官方详情快照纳入岗位级证据门禁。
- `job_hub/contracts.py`：注册新的来源类型和配置约束。
- `job_hub/cli.py`：增加 `sinopec-capture` 运维命令。

## 回滚

回退到 `v0.17.0` 即可移除本阶段代码和快照；本阶段未执行数据库结构迁移。
