# Phase 81 迁移说明

- 无数据库结构迁移。
- 新增 `ATTACHMENT_PROCESS_BATCH_LIMIT` 环境变量，默认值为 `500`；未配置时保持兼容。
- Worker 仍使用现有数据库卷和官方附件哈希存储，不需要重新下载已完成的附件。
- 升级前应备份数据库；升级后可运行 `process-pending-artifacts` 检查未处理队列。
