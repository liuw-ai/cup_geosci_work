# Phase 37 迁移说明

本阶段不新增数据库表，也不需要 SQLite schema migration。`job_events` 已存在，本次只新增 `expired` 事件类型；旧数据库可直接由新代码使用。

部署时先备份 `/var/lib/job-hub/job_hub.sqlite3`，再构建 Web 和 Worker。旧岗位不会被删除；首次新 Worker 同步时会将已经过截止日的 `open` 记录转换为 `expired`，并写入清退事件。
