# Phase 19 迁移说明

## 配置

- `data/sources.json`：将 `sinopec-career.enabled` 从 `false` 改为 `true`。
- 保持 `require_complete_manifest=true` 和 `max_items=500`，防止部分快照被误当成完整来源。

## 数据库

本阶段没有数据库结构迁移。首次同步会将 348 条岗位写入现有 `jobs` 表，并由现有发布门禁计算 `publication_status`；学生端查询条件无需新增代码路径。

## 验证

```text
python -m job_hub.cli sync-source sinopec-career
python -m job_hub.cli audit
python -m pytest -q
```

预期同步结果为 `discovered=348`、`open_matches=69`。若快照缺失、清单不完整或字段证据不完整，同步应失败而不是发布部分岗位。
