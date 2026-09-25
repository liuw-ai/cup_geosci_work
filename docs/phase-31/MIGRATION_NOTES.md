# Phase 31 迁移说明

本阶段没有新增数据库表。已有岗位表接收新的中石化官方快照，使用相同 `external_id` 时执行更新，不重复插入历史岗位。

## 数据文件

- 新增 `data/verified/sinopec-geoscience-20260925.json`，保留 132 个单位和 397 条岗位证据。
- `data/verified/sinopec-geoscience-20260924.json` 保留为历史快照，不覆盖、不删除。
- `data/sources.json` 将中石化来源指向 2026-09-25 快照，并设置最多 1000 条及 30 小时新鲜度门禁。

## 字段修复

中石化页面的完整截止时间原文保留在证据字段；标准职位字段使用 `parse_date_value()` 生成 ISO 日期。无法解析的截止日期不会伪装成有效日期。
