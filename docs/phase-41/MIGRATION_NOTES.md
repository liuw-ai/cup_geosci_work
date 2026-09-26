# Migration Notes

本阶段只增加 `Database.expire_stale_artifact_candidates(as_of=...)` 和 worker 同步调用，不修改 SQLite 表结构，因此无需数据库迁移。旧数据库在升级后会在下一次同步中自动处理已有候选。
