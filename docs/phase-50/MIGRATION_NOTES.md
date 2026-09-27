# Phase 50 迁移说明

本阶段没有数据库 schema 迁移。数据契约迁移如下：

1. 更新 `data/government_position_registry.json` 的 `source_assessments`，新增 `shandong-civil-service-2026` 和 `zhejiang-civil-service-2026` 两条官方公务员扫描记录。
2. 两条记录均为 `verified_scan_no_current_match`，不是 `source_unavailable`；山东保留已截止日期，浙江明确当前遴选面向在编公务员。
3. `records` 不新增公务员岗位行，worker 不会把这两条评估写入学生端。
4. 部署时同步数据文件后运行政府审计命令，确认日报能区分“扫描成功无匹配”和“来源故障”。
