# Phase 86 迁移说明

- 无数据库结构迁移。
- 无岗位回填、删除或学生端展示变更。
- 新增只读命令 `python -m job_hub.cli provincial-matrix-audit`。
- 命令读取现有 `data/source_targets.json`、`data/source_validation_registry.json`、两个来源注册表和运行数据库；不会写入它们。
- 报告中的 `ready_for_activation` 仅表示当前目标、岗位样例、夹具、备用入口和运行状态均满足门禁；不代表本轮存在开放岗位，也不绕过现有学生端专业匹配门禁。
