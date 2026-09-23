# Phase 12 数据迁移说明

本阶段没有新增 SQLite 表，也没有修改数据库 schema。配置和解析代码通过现有 `sources`、`jobs`、`job_evidence`、`crawl_runs` 和 `source_health` 契约工作。

## 运行时修复

使用公开 HTTP 200 缓存回放重新解析四个已核验 CUPB 详情页，按既有 `external_id` 更新岗位，不产生重复记录：

- 赣南实验室：地点更新为“江西省赣州市”。
- 桂林理工大学：地点更新为“桂林”。
- 福建省能源石化创新研究院：用人单位更新为公告标题单位。
- 广西北部湾投资集团：复核单位和地点字段保持不变。

运行时数据库位于 `runtime/job_hub.sqlite3`，不应提交到 Git；服务器部署时运行 `python -m job_hub.cli init`，再按来源队列执行同步。
