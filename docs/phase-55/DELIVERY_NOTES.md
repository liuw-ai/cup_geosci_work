# Delivery Notes

## 版本与回退

本阶段基于分支 `phase/55-cmgb-production-transition`，核心代码提交为 `1239188`。部署前数据库备份位于服务器备份目录；回退时恢复该备份并停用 `cmgb-iguopin-browser`，重新启用快照来源即可。

## 验收命令

```bash
python -m pytest -q
docker compose config --quiet
docker compose exec web python -m job_hub.cli audit
docker compose exec web python -m job_hub.cli coverage --record
docker compose exec worker python -m job_hub.cli worker-health --max-age 300
```
