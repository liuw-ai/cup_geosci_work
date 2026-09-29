# Phase 85 迁移说明

- 无 SQLite 结构迁移，无岗位回填、发布或清退。
- 新增 `cmgb-detail-retry`：从 12 小时内的完整分页 partial 失败归档中，仅重试失败官方详情。
- 新增 `cmgb-quarantine-partial --confirm`：仅用于隔离旧版错误写入正式路径的 partial 文件。命令先归档，后移动为 `*.legacy-partial.json`，可恢复且不会删除。
- `cmgb-browser` worker 默认启用 `retry_partial_first=true` 和 `detail_retry_max_age_hours=12`；这两个参数只属于 `cmgb-iguopin-browser` 来源，不影响中国石油浏览器来源。
- 部署后不要手工复制任何 partial 文件到正式 `captures/cmgb-iguopin-browser.json` 路径。
