# Phase 89 迁移说明

- 无数据库 schema 迁移；Phase 88 的增量字段由应用启动时自动初始化。
- 不替换 Docker volume，不迁移岗位数据。
- 快速镜像仅替换代码和版本化 `data`，不会复制 `.env` 或运行时目录。
- 服务器必须先保留 `phase89` 工作区归档和数据库副本，再切换 Web/Worker。
- 如果任一生产验收失败，回到旧 compose 文件，不删除旧镜像、volume 或备份。

本次服务器备份已验证：

- `/opt/backups/cup_geosci_work-before-phase89-2026-09-29-204848.tar.gz`
- `/opt/backups/job_hub-before-phase89-2026-09-29-204848.sqlite3`
- 运行中数据库 `PRAGMA integrity_check` 返回 `ok`。
