# Phase 88 迁移说明

## 数据库

无需停机导出或重建数据库。`Database.initialize()` 会为既有 `crawl_runs` 追加以下字段，全部为非破坏性默认值：

- `outcome`
- `attempts`
- `retryable_failures`
- `transport_mode`
- `evidence_complete_count`
- `manual_review_count`
- `attachment_success_count`
- `metadata_json`

旧运行记录保留原始状态和数量；没有历史运行上下文的指标使用默认值，账本会按旧状态和错误信息进行保守推断，不会把旧失败记录改写成成功。

## 发布/回退

- 发布前先执行 `python -m job_hub.cli source-run-ledger --date <本地日期>`，确认失败状态未被解释成无岗位。
- 回退代码时数据库新增列可以保留，旧版本会忽略这些列；不需要删除数据。
- 本阶段未修改岗位发布门禁、来源注册表或任何 `.tmp_*` 临时证据文件。

