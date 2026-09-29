# Phase 86 省级来源矩阵审计

本阶段把“31 省 × 五类官方来源”的扩源排期变成一个可重复审阅的只读审计。它不把一个可访问 URL 当成岗位来源，也不把候选、来源故障或扫描无匹配显示为“没有岗位”。

## 新增内容

- `job_hub/provincial_matrix_audit.py`：合并目标矩阵、来源核验台账和运行数据库状态。
- `provincial-matrix-audit` CLI：逐省逐角色输出目标状态、岗位级样例、字段证据、备用入口、来源启用状态、最近健康检查和最近采集状态。
- `tests/test_provincial_matrix_audit.py`：覆盖证据状态与运行状态分离、候选/未定位/阻塞门禁和未知角色校验。

## 使用

在部署工作区运行：

```bash
docker compose exec -T worker python -m job_hub.cli provincial-matrix-audit \
  --output /var/lib/job-hub/reports/provincial-matrix-audit-$(date +%F).json
```

只审计一个省或角色：

```bash
docker compose exec -T worker python -m job_hub.cli provincial-matrix-audit \
  --province 山东 --province 河南 \
  --role public_institution_recruitment
```

报告是管理员内部证据，不进入学生端。只有 `ready_for_review` 的条目才进入下一步人工复核；人工复核完成后，才可以为该来源增加专用适配器或附件流水线。

## 当前边界

本阶段没有新增岗位，也没有把历史已截止职位表重新发布。审计报告显示的是来源建设进度，不是招聘数量。下一步仍需优先处理山东、河南、天津等省份的事业单位公开招聘来源，再等待当年度公务员官方职位表发布后逐行导入。
