# Phase 43 迁移说明

- 无 SQLite schema 迁移。
- 新增 `current_publishable_position_records` 和 `position_record_to_posting`；worker
  每轮同步调用它们，将官方职位表当前明确匹配行写入既有 `jobs` 表。
- `data/provincial_sources.json` 已经登记 `anhui-geology-bureau`，无需重复添加主来源。
- 回滚到 `v0.22.16` 会停止自动桥接，但不会删除已经保存的岗位；回滚后可运行
  `reindex-jobs` 并按截止日期清退，或恢复数据库备份。
