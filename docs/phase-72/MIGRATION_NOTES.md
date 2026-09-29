# Phase 72 迁移说明

无数据库结构迁移。只需更新浏览器 Worker 代码和 Compose 配置；现有
`job_hub_data` 卷、`.env`、历史捕获文件和学生端岗位数据保持不变。

部署时只重建浏览器服务即可：

```bash
docker compose -f docker-compose.browser.yml up -d --build --force-recreate
```

普通 `web` 和 `worker` 服务不需要因为本阶段重建。若新浏览器服务仍失败，保留最后一次成功捕获，故障记录不会覆盖成功快照。
