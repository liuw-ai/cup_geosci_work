# Phase 38 迁移说明

## 数据库

本阶段不新增表、不修改 SQLite schema。岗位编号、官方证据和发布状态沿用现有 \`jobs\`、\`job_events\` 和来源运行记录。

## 部署

部署前备份服务器数据库，然后在服务器项目目录执行：

\`\`\`bash
docker compose config -q
docker compose up -d --build
docker compose exec worker python -m job_hub.cli sync-source cosl-career
\`\`\`

同步后检查来源运行记录、覆盖报告和学生端列表。预期当前广告被标题门禁排除，不应新增“面试信息采集”岗位。

## 回退

若服务器同步审计或学生端检查失败，回退到 \`v0.19.0\` / \`phase-37-review\` 对应提交；本阶段没有破坏性数据库迁移，回退不需要删除数据。
