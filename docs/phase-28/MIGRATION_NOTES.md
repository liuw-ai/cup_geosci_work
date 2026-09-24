# Phase 28 迁移说明

本阶段没有新增 SQLite 表，也没有改变学生端公开岗位字段。

## 捕获 JSON 向后兼容

旧中石化快照可以继续读取。缺失的 `scan_metrics` 会被归一化为：

- `pages_scanned: 0`
- `pages_expected: null`
- `jobs_discovered: null`
- `jobs_exported:` 按岗位详情 URL 绑定结果计数
- `failed_jobs: 0`
- `pagination_complete: false`

因此旧快照可以用于回归测试和私有审计，但不能通过 `--require-scan-complete` 生产门禁。

## 新捕获要求

浏览器 worker 生成的新快照应为每个单位写入 `scan_metrics`，并确保岗位 `detail_url` 中的 hash 路由 `deptId` 与岗位所属单位一致。岗位证据仍须经过原有专业、学历、地点、截止日期和官方域名门禁。

