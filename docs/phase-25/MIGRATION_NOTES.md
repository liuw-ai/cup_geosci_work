# Migration Notes

本阶段没有新增 SQLite 表，也没有破坏性迁移。对已有 `source_artifacts` 的 UPSERT 逻辑做了保护：版本化清单重复登记时，若数据库中的附件已经处于 `downloaded`、`extracted`、`failed` 或 `skipped`，不会被清单的 `registered` 状态覆盖。

日报 payload 新增 `government_quality` 和 `government_positions`，旧日报读取仍兼容。新增环境变量 `GOVERNMENT_ARTIFACT_MANIFEST_PATH`、`GOVERNMENT_POSITION_REGISTRY_PATH` 均为可选项，未设置时使用仓库内的官方清单和职位表台账。
