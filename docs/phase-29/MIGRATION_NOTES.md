# Phase 29 迁移说明

本阶段不新增数据库表、不修改岗位字段、不修改学生端发布门禁，因此不需要数据库迁移。

## 行为兼容

- `JobPipeline.sync_all()` 的 `progress_callback` 为可选参数，现有调用方无需修改。
- 回调抛出的异常会被隔离并记录，来源同步仍按原有逻辑完成。
- Worker 心跳写入使用现有 `service_heartbeats` 表，部署时无需初始化新结构。

## 部署步骤

```bash
git pull --ff-only origin phase/29-worker-heartbeat
docker compose build
docker compose up -d
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
```

如果服务器有未提交的配置文件（例如 `.env`），必须在拉取前单独备份；`.env` 不进入 Git。
