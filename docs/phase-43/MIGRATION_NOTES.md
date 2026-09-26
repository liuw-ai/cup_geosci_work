# Phase 43 迁移说明

- 无 SQLite schema 迁移。
- 新增 `current_publishable_position_records` 和 `position_record_to_posting`；worker
  每轮同步调用它们，将官方职位表当前明确匹配行写入既有 `jobs` 表。
- `data/provincial_sources.json` 已经登记 `anhui-geology-bureau`，无需重复添加主来源。
- 服务器上曾有两个附件候选岗位与台账行重复；已按职位代码修复为 `2026113` 对应
  表格第 17 行、`2026120` 对应第 24 行。后续桥接先按职位代码，再按专业和附件哈希
  去重，避免相邻岗位因专业名称相似而串行。
- 回滚到 `v0.22.16` 会停止自动桥接，但不会删除已经保存的岗位；回滚后可运行
  `reindex-jobs` 并按截止日期清退，或恢复数据库备份。
