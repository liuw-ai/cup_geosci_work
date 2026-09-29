# Phase 76 迁移说明

本阶段没有 SQLite 表结构迁移。

配置变化：

- `cgs-notices.config.allowed_hosts` 增加 `www.drc.cgs.gov.cn`；
- `sinopec-career.config.snapshot_path` 切换到 `data/verified/sinopec-geoscience-20260929.json`；
- 旧的 `sinopec-geoscience-20260928.json` 保留为历史快照，不作为当前来源。

部署顺序：

```bash
docker compose exec -T web python -m job_hub.cli sinopec-capture \
  --path /app/data/verified/sinopec-geoscience-20260929.json \
  --require-complete --require-scan-complete
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
docker compose exec -T web python -m job_hub.cli production-readiness
```

回退到 `phase-75-review` 时恢复旧配置即可，不需要数据库回滚；新快照文件和历史证据不会删除。
