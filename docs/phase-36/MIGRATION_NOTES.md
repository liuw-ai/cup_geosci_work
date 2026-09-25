# 迁移说明

- SQLite 结构新增无；`artifact_job_candidates.location` 原有字段现在允许管理员在私有复核阶段填写公告正文核验地点。
- `data/government_position_registry.json` 将两条安徽岗位从 `manual_review_required` 更新为 `verified_open`，并记录合肥市地址证据。
- `SITE_ATTRIBUTION` 为新增可选环境变量；未配置时使用项目默认归属文字。
- worker 仍会注册并处理政府附件，但不会自动发布未审核候选；本阶段的公开岗位通过受控管理员复核链路产生。

部署后检查：

```bash
docker compose config
docker compose up -d --build
docker compose exec web python -m job_hub.cli government-position-audit --today 2026-09-26
docker compose exec web python -m job_hub.cli audit
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
```

