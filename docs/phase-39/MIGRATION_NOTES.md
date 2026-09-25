# Phase 39 迁移说明

## 数据库

无 SQLite schema 迁移。服务器只更新既有 \`source_artifacts\`、\`source_artifact_rows\` 和 \`artifact_job_candidates\` 记录；岗位候选仍受管理员复核状态控制。

## 运行命令

\`\`\`bash
docker compose exec worker python -m job_hub.cli register-government-artifacts
docker compose exec worker python -m job_hub.cli process-artifact 1
docker compose exec worker python -m job_hub.cli process-artifact 2
docker compose exec worker python -m job_hub.cli process-artifact 3
docker compose exec web python -m job_hub.cli government-position-audit --today 2026-09-26
\`\`\`

这里的 \`1/2/3\` 是数据库附件 ID，不能替换成 manifest 字符串 ID。

## 回退

本阶段不改变学生端公开岗位，不需要数据库回滚。代码回退到 \`v0.20.0\` / \`phase-38-review\` 即可；服务器已保留 Phase 38 SQLite 备份。
