# Phase 34 迁移说明

无需数据库迁移。同步 `data/sources.json` 后重建容器即可。

服务器更新后先运行：

```bash
docker compose exec web python -m job_hub.cli audit
docker compose exec worker python -m job_hub.cli sync-source ccgc-careers
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
```

若新栏目结构再次变化，来源应标记为 `source_degraded` 并进入人工核验，不得把 17 页公告数直接当作岗位数。
