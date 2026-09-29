# Phase 87 迁移说明

- 无数据库结构迁移。
- 无岗位回填、删除或学生端 UI 变更。
- `government-position-audit` 新增读取现有 `government_source_verifications` 的运行状态。
- 数据库中没有复核记录时，仍按版本化台账的新鲜度规则处理；不会把缺失复核记录解释为来源成功。
- 生产 worker 的行为未改变，只是管理员审计命令现在与其共享同一份复核状态。
