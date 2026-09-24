# Migration Notes

本阶段没有新增 SQLite 表，也没有改变学生端数据契约。`SourceHealthResult` 增加了可选的 `checks` 诊断字段，`source-health` 增加 `--output`，用于保存管理员私有诊断 JSON。

现有 `source_health` 表仍只保存来源级状态、状态码、时间和摘要；详细的 robots/入口检查结果由管理员按次保存到运行目录，避免把大量诊断细节混入公开数据库。
