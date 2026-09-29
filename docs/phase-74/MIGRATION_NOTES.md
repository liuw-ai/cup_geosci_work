# Phase 74 迁移说明

## 数据库

无 schema migration。备份快照和附件一样保存在应用私有数据目录；不进入 Git、不被 Web 静态目录挂载。

新增 `.env` 配置：

```dotenv
BACKUP_STORAGE_DIR=backups
BACKUP_RETENTION_DAYS=14
BACKUP_MIN_INTERVAL_MINUTES=720
```

`BACKUP_STORAGE_DIR` 必须位于 `APP_DATA_DIR` 下。默认每 12 小时在同步前生成一次经过 SQLite 完整性和核心表验证的快照，保存 14 天；实际存储量应按生产数据库大小和可用磁盘空间复核。

## 部署前检查

1. 备份当前数据库和代码目录；
2. 将新 `.env` 键写入服务器 `.env`，不要提交 SMTP 密码、管理员令牌或私钥；
3. 重建 Web 和 Worker；
4. 强制创建首份受管备份；
5. 检查 Web、Worker 和备份状态；
6. 仅在 DNS 已正确解析后启动 Caddy 的 HTTPS 验收。

```bash
docker compose build web worker
docker compose up -d web worker
docker compose exec -T web python -m job_hub.cli backup-database --force
docker compose exec -T web python -m job_hub.cli worker-health --max-age 180
docker compose exec -T web python -m job_hub.cli audit
```

## 恢复演练

恢复会替换当前数据库，必须先停止所有写入者：

```bash
docker compose stop web worker
docker compose run --rm web python -m job_hub.cli verify-backup \
  /var/lib/job-hub/backups/job_hub-YYYYMMDDTHHMMSSZ.sqlite3
docker compose run --rm web python -m job_hub.cli restore-database \
  /var/lib/job-hub/backups/job_hub-YYYYMMDDTHHMMSSZ.sqlite3 --confirm
docker compose up -d web worker
```

恢复命令只接受 `BACKUP_STORAGE_DIR` 内通过验证的快照，且会尽力先创建恢复前紧急备份。恢复后必须重新运行 `audit`、`worker-health` 和 `production-readiness`。
