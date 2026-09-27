# Phase 52 迁移说明

## 数据库

本阶段没有数据库结构迁移。现有 `postings` 表继续保存标准化岗位，`publication_status` 继续由学生端专业/学历门禁计算；快照来源通过 `data/sources.json` 的 `official_snapshot_rows` 配置接入。

## 数据文件

- 新增：`data/verified/cmgb-iguopin-geoscience-20260927.json`
- 更新：`data/sources.json` 中 `cmgb-iguopin-2027-geoscience-snapshot` 的 `snapshot_path` 和扫描说明。

快照中的 `source_url` 为逐岗位官方国聘详情地址，`official_evidence_url` 为总局官方公告，`field_evidence.evidence_scope` 固定为 `official_detail_block`。所有发布所需字段都必须从岗位详情块取得，不能由列表摘要补齐。

## 回滚

```bash
git revert <phase-52-commit>
```

或者回退到 `v0.22.40` / `phase-51-cmgb-geoscience-capture`。回滚不会删除数据库历史记录，只会恢复来源配置和代码版本。
