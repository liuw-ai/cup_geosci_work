# Migration Notes

本阶段没有新增 SQLite 表，也没有修改已有表结构。运行 `register-government-artifacts` 会向已有 `source_artifacts` 表登记三条私有附件元数据，状态为 `registered`，不会下载附件、解析行或创建学生端岗位。

服务器部署后可重复运行该命令，数据库使用 `(source_id, artifact_url)` 幂等更新，不会重复创建同一附件。附件处理仍由 `OfficialAttachmentProcessor` 负责，并保留 SHA-256、存储路径、解析器版本和行级证据。
