# Phase 107: Official position-table activation tasks

## Why this phase exists

上一阶段已经把中国地震局 2027 批次的 91 条地学岗位行保存为官方待开放台账：
公告和附件已核验，但报名窗口要到 2026-10-10 才开始。原有系统能够阻止提前发布，
也能够在人工复核后按日期激活，却没有把“开放日必须复核”作为显式的运维任务。
这会让一个合法的未来批次在开放日到来后因缺少人工确认而静默留在台账中。

## Change

- `job_hub.government_positions.source_activation_tasks` 按来源汇总带 `opening_date` 的明确匹配岗位。
- 任务状态分为：
  - `scheduled`：尚未到报名开始日，不能进入学生端；
  - `due_revalidation`：已到开放日但没有新鲜的官方证据复核，必须人工复核公告和附件；
  - `verified`：开放后已有新鲜的官方证据复核，可以由既有发布门禁处理；
  - `expired`：该批次所有岗位已超过截止日，应确认学生端清退。
- 任务只进入政府职位质量报告和管理员日报邮件，不启用来源、不绕过 robots、不自动发布未复核岗位。
- 每个任务保留岗位行数、计划人数、开放日、截止日和最近复核时间，便于服务器上直接核对。

## Verification

```text
python -m pytest -q tests/test_government_positions.py
16 passed
python -m pytest -q
457 passed, 1 skipped
python -m compileall -q job_hub tests
git diff --check
```

## Current effect

本阶段没有虚增当前岗位数量，也没有把历史职位表转成在招岗位。当前生产基线仍为约
`72/100`、146 条学生端岗位；中国地震局批次在 2026-10-10 前显示为 `scheduled`，
开放日若未完成官方复核会显示为 `due_revalidation`，复核成功后才允许按原有门禁进入学生端。

## Next acceptance gate

下一阶段仍需产生新的、可连续复核的当前官方岗位来源：

1. 在中国地震局报名开放日完成公告和附件复核，确认 91 条岗位的开放状态、字段和官方链接；
2. 至少新增两个省份的当前事业编官方职位表，连续两次刷新成功并验证过期清退；
3. 至少新增一个三桶油下属单位的岗位级官方详情链路；
4. 不满足以上条件前，不把测试数量或候选入口数量计入完成度，也不宣称达到 75 分。
