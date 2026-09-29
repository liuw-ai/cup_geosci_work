# Phase 78 交付说明

本阶段解决附件岗位“后台解析有字段、学生端发布后字段消失”的问题，并为甘肃等 PDF 断行职位表提供可审计的人工修正路径。没有导入历史公务员职位表，也没有使用模拟岗位。

阶段标签：`phase-78-review`。

服务器部署后必须执行：

```bash
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
docker compose exec -T web python -m job_hub.cli production-readiness
```
