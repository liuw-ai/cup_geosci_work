# Migration Notes

数据库迁移是 SQLite 增量迁移，不需要删除旧库。

首次启动新版应用时，`jobs` 表会增加：

```text
field_evidence_json TEXT NOT NULL DEFAULT '{}'
```

旧岗位会得到空证据对象，因此不会自动升级为“明确匹配”。生产部署前仍应备份 `APP_DATABASE_PATH`，启动后运行 `audit`，再按来源逐个重新同步。

worker 每次来源同步后会处理 `registered` 附件；`needs_review`、`rejected` 和已发布状态不会重复下载。附件行仍须管理员核验后才能进入公开岗位表。
