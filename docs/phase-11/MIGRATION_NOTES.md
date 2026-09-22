# Phase 11 迁移说明

## 数据库

本阶段没有新增数据库表或破坏性迁移。运行时继续使用已有的 `source_artifacts`、`source_artifact_rows`、`artifact_job_candidates` 和 `job_evidence` 表。

附件候选仍遵循：

```text
官方公告页 -> 受控下载/哈希 -> 表格行 -> needs_review -> official_content_verified -> 公开岗位
```

未通过人工核验的 22 行不会出现在学生端。

## 配置与依赖

- `data/sources.json` 新增 `cmgb-geoexp-recruitment`，并为该来源显式设置 `allow_shared_source_url: true`。
- `data/provincial_sources.json` 为安徽地质局来源显式设置 `allow_shared_source_url: true`，因为同一官方公告页同时承载正文岗位和附件岗位。
- `data/organization_registry.json` 增加中国冶金地质总局地球物理勘查院层级与正式招聘频道。
- `requirements.txt` 增加 `xlrd==2.0.2`，用于传统 `.xls`。
- `SUPPORTED_SUFFIXES` 增加 `.xls`。
- `cupb_career` 适配器新增 `candidate_limit`、`listing_exclude_patterns` 和 `detail_exclude_patterns` 配置；无需数据库表迁移，执行 `init` 会把来源配置更新到现有 `sources` 表。

部署时执行 `pip install -r requirements.txt`。已有 SQLite 数据库会由现有初始化迁移逻辑自动兼容，不需要手工改表。

## 回滚

代码回滚到 `phase-10-review` 即可撤销本阶段适配器和解析器；运行时新增岗位/候选属于数据库数据，不应通过 Git 回滚文件恢复，生产环境应按数据库备份策略回退。
