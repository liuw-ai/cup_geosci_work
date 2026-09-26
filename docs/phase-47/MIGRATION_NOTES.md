# Phase 47 迁移说明

本阶段没有新增 SQLite 表。升级步骤：

1. 备份服务器 SQLite 数据卷。
2. 更新 `data/government_artifact_manifest.json` 和 `data/government_position_registry.json`。
3. `docker compose up -d --build web worker`，worker 会自动幂等注册附件并同步 4 条宁夏岗位。
4. 检查 `government-position-audit`、`audit` 和 `worker-health`。

回退时停止 worker，切换到上一个提交/标签并恢复数据库备份；不要在 worker 写入时覆盖 SQLite。
