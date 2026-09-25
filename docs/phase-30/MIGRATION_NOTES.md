# Phase 30 迁移说明

本阶段没有数据库迁移，没有新增岗位字段，也没有改变学生端发布门禁。

## 配置迁移

- 本地 `.env` 可继续使用默认的 `127.0.0.1:8080`。
- 服务器 `.env` 增加 `WEB_BIND_ADDRESS` 和 `WEB_PORT`；这两个值不包含密钥。
- 服务器上的 `APP_SECRET_KEY`、`ADMIN_TOKEN` 和数据库卷保持不变。

回滚时将服务器 `.env` 恢复为 `WEB_BIND_ADDRESS=127.0.0.1`、`WEB_PORT=8080`，再执行 `docker compose up -d`，即可恢复仅本机访问。
