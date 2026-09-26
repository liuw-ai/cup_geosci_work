# Phase 47 质量报告

- 基线提交：`753b2a4`（Phase 46）
- 阶段分支：`phase/47-open-government-position-tables`
- 核验日期：2026-09-27
- 本地测试：`285 passed`
- 新增学生端政府岗位：4 条宁夏官方岗位
- 当前政府职位台账：6 条 `verified_open`，6 条明确专业匹配
- 宁夏附件：4 行岗位，岗位代码 001、002、003、004；每岗 1 人；截止 2026-12-31；研究生/博士；官方 XLS 行证据完整
- 甘肃调整版 PDF：25 个版式行、8 个地学候选；因跨列错位保留 `needs_review`，未发布

服务器验收需执行：

```bash
docker compose exec worker python -m job_hub.cli government-position-audit --today 2026-09-27
docker compose exec worker python -m job_hub.cli audit
docker compose exec worker python -m job_hub.cli worker-health --max-age 300
```
