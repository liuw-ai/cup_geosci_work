# Phase 40 迁移说明

## 数据库

无 SQLite schema 迁移。新增来源记录由现有 `sources` 表的 JSON 配置自动 upsert；捕获文件存储在 `APP_DATA_DIR/captures/cnpc-jobs.json`，不进入学生端静态资源。

## 部署

普通服务仍使用 `docker-compose.yml`。服务器启用中国石油浏览器采集时，额外运行：

```bash
docker compose -f docker-compose.browser.yml up -d --build
```

该文件与主 compose 共享 `job_hub_data` 卷。浏览器 Worker 不拥有数据库写入以外的应用管理权限，也不调用报名或登录接口。

