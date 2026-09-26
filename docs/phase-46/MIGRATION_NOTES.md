# Phase 46 迁移说明

本阶段没有数据库 schema 迁移。`PARSER_VERSION` 从 `attachments-v1` 升为
`attachments-v2`，用于让已处理附件在需要时重新运行新版 PDF 解析器；原始文件哈希、
公告链接、附件链接、页码/行号、字段证据和审核状态均保留。

部署步骤：

1. 备份服务器 SQLite 数据库和 `official-attachments` 数据卷。
2. 部署代码并重建 worker/web 镜像。
3. 执行政府附件刷新和甘肃附件处理；只处理 `registered` 或显式重跑的附件。
4. 执行过期清退与 `government-position-audit`，确认甘肃历史岗位不在学生端。
5. 检查 worker 健康、日报和管理员复核队列后再创建版本标签。

回退到上一版本不会删除数据；旧版本可能无法读取 v2 的新解析元数据，回退前必须保留
完整数据库和附件卷，不要只复制 SQLite 的单个 WAL 文件。
