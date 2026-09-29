# Phase 75 迁移说明

本阶段没有 SQLite 表结构迁移，也不修改已发布岗位、来源或附件原件。

部署后，Worker 的既有 `reindex_jobs()` 会重新运行学生发布门禁。任何已保存的官方附件岗位行若在 `row_text` 中明确包含多年工作经验要求，将被重新判定为 `out_of_scope` 并从学生端隐藏；原始岗位、附件和审计事件会保留，便于管理员复核。

建议上线后执行：

```bash
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
docker compose exec -T web python -m job_hub.cli production-readiness
```

回退到 `phase-74-review` 不需要数据回滚；数据库中原始附件候选和证据记录均保持不变。
