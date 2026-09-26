# Phase 45 迁移说明

本阶段没有数据库 schema 变更，也没有新增岗位数据迁移。服务器仅执行了来源同步、
浏览器服务重启和审计，已有历史记录与过期清退规则保持不变。

部署时：

1. 先备份 `/var/lib/job-hub/job_hub.sqlite3` 与 `official-attachments` 卷。
2. 部署代码后运行 worker 健康检查和审计。
3. DNS 记录生效后再运行 `domain-check`，不要手工修改 Caddy 证书数据。
4. CNPC/中石化捕获必须生成新的、完整的官方证据文件后才允许发布岗位。
