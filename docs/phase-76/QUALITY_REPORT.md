# Phase 76 质量报告

## 真实捕获结果

| 指标 | 结果 |
| --- | ---: |
| 中石化单位总数 | 132 |
| 已捕获单位 | 132 |
| 地质候选单位 | 35 |
| 候选单位详情完成 | 35 |
| 岗位行 | 363 |
| 失败岗位行 | 0 |
| 候选单位分页完成 | 35/35 |
| 未知单位绑定行 | 0 |

官方平台：`https://job.sinopec.com/#/school/recruitmentPositions`。

## 自动化验证

已执行：

```text
python -m pytest -q: 371 passed
python -m compileall -q job_hub: passed
docker compose config: passed
docker compose -f docker-compose.browser.yml config: passed
git diff --check: passed
python -m job_hub.cli sinopec-capture --path data/verified/sinopec-geoscience-20260929.json --require-complete --require-scan-complete: passed
```

新增回归覆盖：

- 详情白名单主机允许通过审计，外部主机仍被拒绝；
- 最新中石化快照的 132/35/363 完整性和分页门禁；
- 管理端中石化捕获统计随最新快照更新。

## 当前未通过项

本地代码门禁已通过，但服务器最终 `production-readiness` 仍需在新代码和新快照部署后重新执行。中石化目前仍是受控快照来源，尚未形成独立的每日自动捕获 Worker；因此本阶段不能宣称全院正式版已经完成。
