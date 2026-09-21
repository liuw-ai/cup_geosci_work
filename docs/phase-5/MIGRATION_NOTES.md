# Phase 5 迁移说明

## 数据库变更

本阶段是 SQLite 加法迁移，不删除或重写现有岗位、来源、日报、抓取运行、附件和证据记录。

`candidate_leads` 新增：

| 字段 | 用途 | 旧数据处理 |
| --- | --- | --- |
| `discovery_source_id` | 关联私有发现渠道 | 旧线索为 `NULL` |
| `lead_fingerprint` | 规范化 URL 的稳定去重键 | 初始化时为每条旧线索回填 |
| `official_source_id` | 关联正式来源注册表 | 旧线索为 `NULL` |
| `official_domain_status` | 未核验、来源匹配、人工批准或不匹配 | 默认 `unverified` |
| `official_checked_at` | 最近一次官方域名评估时间 | 旧线索为 `NULL` |

新增 `candidate_lead_mentions` 表，记录同一线索被不同发现渠道提及的 URL、时间和私有元数据；删除线索时按外键级联删除。

## 迁移特性

- 启动时先执行加法列迁移，再创建依赖新列的索引，兼容 Phase 1 至 Phase 4 的旧库。
- 指纹回填只使用原有 `lead_url`、标题和单位提示，不改写线索正文或状态。
- 重复导入返回已有线索，并新增/更新一个渠道 mention，不会创建公开岗位。
- 迁移可回退方式：停止服务后备份 SQLite 文件；回退代码版本不会删除新增列，旧代码会忽略未知列。若需要完全恢复，使用迁移前数据库副本。

## 运维检查

```powershell
python -m job_hub.cli init
python -m job_hub.cli discovery-sources
python -m job_hub.cli discovery-funnel
python -m pytest -q
```

本阶段没有数据删除、没有岗位重算、没有网络同步。
