# Phase 1 SQLite 迁移说明

## 迁移性质

这是一次可重复执行的加法迁移。`Database.initialize()` 先执行 `CREATE TABLE IF NOT EXISTS`，再执行兼容迁移和回填。它不删除表、不删除岗位、不改写岗位正文、不调整日报、也不运行爬虫。

新增表：

| 表 | 数据 | 删除关联规则 |
| --- | --- | --- |
| `source_artifacts` | 官方附件的私有元数据 | 删除来源时级联删除。 |
| `job_evidence` | 岗位的官方页面、招聘记录、附件或字段证据 | 删除岗位时级联删除；删除附件时只清空 `artifact_id`。 |
| `candidate_lead_events` | 私有线索的状态历史 | 删除线索时级联删除。 |

新增索引覆盖来源与附件状态、岗位证据与验证状态、附件关联、线索历史查询，不修改原有岗位查询索引。

## 旧库回填

启动迁移时：

1. 如果旧 `jobs` 表缺少 `official_evidence_url`，沿用既有逻辑从 `source_url` 填充。
2. 对每一个尚未拥有已核验官方页面/记录证据、且拥有有效 HTTP(S) 官方链接的旧岗位，新建一条 `official_page` 证据索引。
3. 对每一个没有历史事件的旧私有线索，新建一条不可变的 `status_snapshot` 事件。

回填使用稳定证据键和事件键；重复启动不会复制行，也不会覆盖已经存在的岗位证据。

## 迁移前后核对

建议在部署前先备份持久化数据目录，再运行应用初始化：

```bash
cp runtime/job_hub.sqlite3 runtime/job_hub.sqlite3.pre-phase-1.bak
python -m job_hub.cli init
python -m job_hub.cli audit
```

生产环境使用 Docker Compose 时，应在停止写入进程后复制宿主机挂载的数据卷或用 SQLite 在线备份 API 创建一致性备份；不要复制正在被写入的单个 `-wal` 文件。

## 回退

Phase 1 不修改原表中的招聘正文或日报，因此代码回退可精确切换到 `v0.4.0`。如果已经开始使用新的私有附件/证据/事件表，回退前应保留完整 SQLite 备份：旧版本不会读取这些新表，但简单删除它们会丢失 Phase 1 后新增的管理员审计数据。

正式部署前，先在数据库副本上执行升级、审计和恢复演练；Phase 2 之前不应创建附件下载目录或保存任何附件正文。
