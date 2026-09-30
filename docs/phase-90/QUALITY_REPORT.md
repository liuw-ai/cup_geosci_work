# Phase 90 质量报告

## 代码验收

- 连续失败计数与历史尝试次数分离；
- 普通失败和访问策略限制使用不同退避上限；
- 成功后连续失败计数归零；
- 旧数据库可通过加列迁移；
- 现有失败状态和公开岗位门禁不改变；
- 新增队列、迁移和退避回归测试。

## 真实性边界

本阶段没有新增岗位数量，也没有把 `source_unavailable`、`access_limited` 或 `parse_failed` 转换成无岗位或开放岗位。它只提高来源运行的稳定性；国内岗位扩容仍必须逐来源完成官方详情和字段证据核验。

## 服务器验收记录（2026-09-30）

- 部署提交：`faf96c6`，镜像 `cupb-geoscience-job-hub:phase90-fast`；
- SQLite 备份：`/opt/backups/job_hub-before-phase90-2026-09-30-105637.sqlite3`；
- 项目归档：`/opt/backups/cup_geosci_work-before-phase90-2026-09-30-105637.tar.gz`；
- 备份 `PRAGMA integrity_check`：`ok`；
- Web/Worker：均为 `healthy`；
- Worker health：`ok=true`；
- `source_tasks`：`succeeded=38`、`blocked=3`，返回连续失败字段；
- 2026-09-30 运行账本：`success_with_matches=28`、`success_without_matches=64`、`source_unavailable=8`、`access_limited=4`、`parse_failed=0`、`manual_review_required=56`、`unknown=0`；
- 该运行账本的 `open_matching_count=508`、`evidence_complete_count=508`、`attachment_success_count=168`，仅表示完成运行中的统计，不等同于 508 条学生端新增岗位；
- 数据库迁移检查：`source_tasks.consecutive_failures` 已存在；
- 浏览器容器和 Caddy 未被删除，`job_hub_data` volume 未替换；
- 正式域名/DNS 仍未验收，本阶段不宣称 `jobs.cupdky.cn` 可用。
