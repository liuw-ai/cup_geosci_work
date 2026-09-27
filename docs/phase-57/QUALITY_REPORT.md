# Quality Report

## 本地验证

- `python -m pytest -q`：316 passed。
- `python -m compileall -q job_hub`：通过。
- `git diff --check`：通过。
- `government-position-audit --today 2026-09-28 --max-age-hours 48`：台账年龄 24 小时，`registry_freshness=fresh`，122 条当前岗位。
- 同一台账在模拟日期 2026-09-30（年龄 72 小时）返回 `registry_freshness=stale`，可发布行数为 0，验证过期快照不会继续展示。

## 服务器基线（改动前复核）

- 服务：`web`、`worker`、`cnpc-browser`、`cmgb-browser` 均运行。
- 数据库审计：`ok=true`，1424 条总记录，268 条公开岗位，0 个审计问题。
- 政府职位台账：136 条记录、122 条当前明确匹配、14 条已关闭、来源故障/待核验 0。

## 部署后复核

- 镜像已在服务器重建，`web` 健康、`worker-health` 通过。
- worker 同步日志：政府职位 `unchanged=122`、`withdrawn=2`、`error=0`。
- 数据库审计：`ok=true`，1424 条总记录，266 条公开岗位，0 个审计问题。
- 模拟 2026-09-30 的过期台账报告同时显示 `registry_freshness=stale`、`verified_open_records=0`，与学生端发布门禁一致。
