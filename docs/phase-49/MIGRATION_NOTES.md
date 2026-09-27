# Migration Notes

1. `government_position_registry.json` 增加湖南省直事业单位第四次招聘 14 条逐岗位历史证据，状态为 `verified_closed`，截止日为 `2026-09-16`。
2. `government_artifact_manifest.json` 增加对应官方 XLSX，状态为 `historical_closed`。
3. `source_validation_registry.json` 增加湖南地质院服务器直连扫描记录，并记录内部公开选拔不面向社会毕业生的排除理由。
4. `job_hub/government_positions.py` 新增 `verified_scan_no_current_match` 来源评估状态和质量报告计数；不改变学生端发布门禁。
5. 无数据库结构迁移。部署后运行政府审计和正常 `publish --refresh`，历史记录会被截止日门禁排除。
