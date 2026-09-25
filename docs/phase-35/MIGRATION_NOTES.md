# Phase 35 迁移说明

仅更新 `data/government_position_registry.json` 的国家公务员来源状态和核验说明，无数据库迁移、无岗位导入。

部署后执行：

```bash
docker compose exec web python -m job_hub.cli government-position-audit --today 2026-09-25
docker compose exec web python -m job_hub.cli audit
```
