# Phase 16 Migration Notes

## 数据迁移

1. 执行前保留运行库备份；本阶段只更新 `artifact_job_candidates.field_evidence_json`、已发布岗位的 `jobs.field_evidence_json` 和派生发布状态。
2. 运行 `python -m job_hub.cli repair-attachment-evidence`。
3. 命令只处理已登记的官方附件候选；`needs_review`、`official_content_verified` 等审核状态不被自动推进到 `published`。
4. 对已经存在 `published_job_id` 的候选，补写同一附件行的岗位级证据，再调用现有 `reindex_jobs` 重新计算学生端资格。
5. 用 `python -m job_hub.cli publish --refresh` 更新当天日报，避免日报保留迁移前快照。

## 回退

本阶段代码可回退到 `v0.15.0`。数据库字段变更是加性 JSON 更新；如需恢复旧运行库，应停止 worker 后用备份 SQLite 替换当前运行库，不要在写入过程中复制数据库文件。

## 证据边界

本阶段没有下载新的受限附件，没有绕过 robots、TLS、验证码或登录，也没有把候选池直接暴露给学生。只有此前已人工核验的 2 条安徽官方附件岗位因证据格式修复重新公开。

