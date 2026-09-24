# Phase 28 质量报告

日期：2026-09-25  
分支：`phase/28-sinopec-dedicated-adapter`

## 测试

```text
python -m pytest -q tests/test_sinopec_spa.py tests/test_sinopec_scan.py
12 passed

python -m pytest -q
243 passed

python -m compileall -q job_hub
passed
```

## 真实官方快照审计

输入：`data/verified/sinopec-geoscience-20260924.json`，官方平台为 `job.sinopec.com`。

| 指标 | 结果 |
| --- | ---: |
| 单位清单 | 132 |
| 关键词候选单位 | 35 |
| 已捕获候选详情 | 35 |
| 岗位行 | 348 |
| 岗位行绑定已知单位 | 348 |
| 候选单位分页完成证据 | 0/35 |
| 严格生产门禁 | 未通过 |

“候选详情 35”只表示快照中存在 35 个官方详情路由和岗位行；由于旧快照没有逐单位分页完成指标，本阶段正确地将其报告为未完成，而不是宣称 35 个单位已经完成实时扫描。当前岗位专业匹配仍由既有学生端发布门禁决定。

## 失败分类

单位状态可以区分 `success`、`scan_success_no_match`、`access_limited`、`parse_failed`、`manual_review_required` 和进行中的列表状态。访问受限、解析失败和人工复核不会进入“扫描成功无匹配”。

## 未完成事项

云服务器和公开浏览器运行环境尚未提供，因此本阶段没有伪造在线采集结果。下一阶段需要在服务器执行计划，回填每个单位的分页指标和岗位级字段证据，再重新运行严格门禁。

