# Phase 79 服务器验收记录

本阶段代码已完成本地回归，尚未切换生产工作区。切换服务器时应使用独立工作区，先备份数据库和项目目录，再构建 Web/Worker 镜像并执行：

```bash
docker compose exec worker python -m job_hub.cli sync-source slb-career
docker compose exec worker python -m job_hub.cli source-tasks --due-only
docker compose exec worker python -m job_hub.cli audit
```

验收重点是 `slb-career` 的当前任务状态是否从旧 `failed` 收敛为 `succeeded`，以及原失败 `crawl_runs` 是否仍可审计。域名 DNS 不在本阶段擅自修改。
