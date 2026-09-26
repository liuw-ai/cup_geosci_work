# Phase 44 迁移说明

## 代码

- 无数据库结构迁移。
- `job_hub/worker.py` 增加同轮政府职位表行的已用记录排除集合，避免两个职位代码互相覆盖。
- `job_hub/cli.py` 在动态浏览器捕获命令的早期分支中初始化 `services()`，避免 `UnboundLocalError`。

## 部署

```bash
docker compose build web worker
docker compose up -d web worker
docker compose -f docker-compose.yml -f docker-compose.browser.yml up -d cnpc-browser
```

部署后检查：

```bash
docker compose exec web python -m job_hub.cli audit
docker compose exec web python -m job_hub.cli worker-health --max-age 180
docker compose exec web python -m job_hub.cli domain-check --hostname jobs.cupdky.cn --expected-ip 81.70.62.174
```

## 回滚

恢复到 `v0.22.25` 可移除本阶段 CLI 初始化修复；恢复到 `v0.22.24` 可同时移除政府同轮去重修复。数据库数据卷不需要回滚。

