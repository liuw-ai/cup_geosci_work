# Phase 44 迁移说明

- 无 SQLite schema 迁移。
- `data/provincial_sources.json` 启用 `shandong-hrss-exam` 和 `gansu-geology-bureau`，并记录服务器验证日期。
- `data/source_targets.json` 将山东人事考试和甘肃地矿局目标槽位从 `candidate` 提升为 `verified`。
- `data/source_validation_registry.json` 记录山东入口的服务器直连证据，并将北京既有记录的状态与契约同步。
- `job_hub/contracts.py` 与 `job_hub/source_validation.py` 支持并统计 `server_health_and_adapter_verified`。
- 回滚本阶段只需回退代码和 JSON；不会删除数据库中已经保存的岗位。需要清理错误数据时必须使用现有精确来源清理/回滚流程，并先备份数据库。

## 部署注意

服务器当前仓库可能存在未提交的运行环境改动，部署时只替换上述受控文件并重新构建容器，不执行 `git reset --hard` 或覆盖 `.env`、数据库卷和 Caddy 证书卷。
