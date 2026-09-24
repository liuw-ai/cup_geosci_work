# Migration Notes

本阶段没有修改 SQLite 表结构，也没有覆盖运行库。政府职位表先以版本化 JSON 证据台账进入代码库，避免把未复核的附件行直接写入生产岗位表。

部署时无需数据库迁移。升级代码后可运行：

```text
python -m job_hub.cli government-position-audit --today 2026-09-25 --output runtime/phase23-government-position-quality.json
```

后续人工复核通过的职位行，仍须经现有 `OfficialAttachmentProcessor`、`JobPipeline.normalize_posting` 和学生端专业发布门禁写入 SQLite；不能直接编辑生产数据库。
