# Migration Notes

## 数据库

未新增表结构。生产切换使用既有原子同步事务：动态来源写入完成后，旧快照岗位保留证据并标记为 `superseded`，学生端不重复展示。

服务器数据库切换前备份仍保留在：

`/home/ubuntu/backup/job_hub-pre-phase55.sqlite3`

## 运行时

`docker-compose.yml` 为 `web` 和 `worker` 增加：

`./tests/fixtures/source_validation:/app/tests/fixtures/source_validation:ro`

这是质量报告所需的版本化离线夹具，不包含学生个人数据，也不改变公开岗位发布规则。
