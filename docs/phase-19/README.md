# Phase 19：中石化岗位正式发布

本阶段将 Phase 18 的中石化官方 SPA 快照接入正式同步流程。快照仍来自中石化官方招聘系统，服务器不绕过其访问策略；在公开只读接口完成验证前，按版本化快照周期运行。

## 发布边界

- 快照来源 `sinopec-career` 已启用。
- 348 条岗位全部写入内部数据库，保留用于审计、去重和后续复核。
- 学生端仅展示 `student_eligible` 或 `unrestricted_eligible`。
- 本次学生端新增 69 条明确匹配岗位。
- 98 条“相关专业”等模糊条件岗位保持 `pending_evidence`，181 条专业不匹配岗位保持 `out_of_scope`。

## 运行方式

```text
python -m job_hub.cli sync-source sinopec-career
python -m job_hub.cli audit
python -m job_hub.cli simulate-cohort
```

同步前应确认 `data/verified/sinopec-geoscience-20260924.json` 已随版本部署。快照更新必须重新运行 `sinopec-capture --require-complete` 和岗位级门禁审计。

## 官方证据

- 单位列表：[中石化招聘信息](https://job.sinopec.com/#/school/recruitmentPositions)
- 示例详情：[胜利油田 2027 年校园招聘](https://job.sinopec.com/#/school/recruitEnterpriseDetail?deptId=BDA2ACEB-9C93-41B9-BFCB-887C71FB4C75)

## 回滚

将 `sinopec-career.enabled` 恢复为 `false` 并重新部署配置即可停止后续同步；数据库中的历史记录不会被删除。若需恢复本地同步前状态，可使用 `runtime/job_hub.pre-sinopec-promotion-20260924.sqlite3`，服务器应使用部署前数据库备份。
