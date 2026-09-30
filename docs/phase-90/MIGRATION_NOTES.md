# Phase 90 迁移说明

## 数据库

`Database.initialize()` 为既有 `source_tasks` 表增加：

```sql
consecutive_failures INTEGER NOT NULL DEFAULT 0
```

迁移是加列操作，不删除岗位、运行账本、证据或备份。成功任务会把该字段清零，失败任务递增该字段。

## 回退

回退到 Phase 89 代码时，保留新增字段不会影响旧代码读取。部署前后的 SQLite 备份必须保留；回退只替换应用镜像，不删除 `job_hub_data` volume。
