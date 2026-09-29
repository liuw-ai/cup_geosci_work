# Phase 79 来源任务状态收敛与政府来源质量门禁

本阶段处理每日更新链路中的一个实际运维缺陷：管理员使用 `sync-source` 成功复测来源后，旧的 `source_tasks.status=failed` 仍会保留，监控因此把已恢复来源继续报成失败。

## 本阶段完成

- 新增 `JobPipeline.sync_source_manual()`，手工同步与定时批量同步使用同一套成功/失败队列收敛规则。
- 手工同步先取得强制调度租约；已有 Worker 租约时返回 `deferred`，不并行采集同一来源。
- 成功手工同步会记录最新 `run_id`、清空当前错误并安排下一次同步。
- 失败手工同步会记录错误分类、重试时间和最新 `run_id`。
- 历史 `crawl_runs` 不删除、不覆盖，旧失败仍可审计。
- 维持政府职位表的四种业务含义：`verified_open`、`verified_scan_no_current_match`、`source_unavailable`、`manual_review_required`；扫描成功无匹配不得被解释为来源故障。

## 验收标准

1. 手工成功复测后，来源任务当前状态为 `succeeded`，不再残留旧失败错误。
2. 手工失败后，来源任务为 `failed` 或 `blocked`，且保留错误分类与重试时间。
3. 历史失败 `crawl_runs` 仍存在，不能通过更新队列状态抹掉证据。
4. 仅有官方岗位级证据、明确专业和学历匹配、且处于报名期的政府职位进入学生端。
5. DNS 尚未恢复前，系统继续标记 `public_ready=false`，不能把 IP 入口冒充正式域名。
