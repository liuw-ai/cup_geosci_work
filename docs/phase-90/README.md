# Phase 90：来源自适应重试与服务器运行闭环

## 目标

本阶段不新增未经核验的岗位，也不绕过 robots、403、验证码或其他访问策略。目标是让已有来源队列在服务器上持续运行时，能够区分临时解析失败与访问策略限制，采用可审计的退避策略，并在成功后恢复正常节奏。

## 变更

- `source_tasks.consecutive_failures` 记录连续失败次数，和历史总尝试次数分开；
- 普通采集/解析失败采用有上限的指数退避，默认 5 分钟起、最长 6 小时；
- 访问策略限制默认 6 小时起、最长 24 小时；
- 成功同步后连续失败计数清零；
- 旧 SQLite 通过加列迁移，不改写岗位、运行历史和来源证据；
- 管理员 `/api/admin/source-tasks` 自动返回新的连续失败字段；
- 服务器仍保留失败来源状态，不能将失败解释为“无岗位”。

## 配置

可在 `.env` 中覆盖：

```dotenv
SOURCE_RETRY_BASE_SECONDS=300
SOURCE_RETRY_MAX_SECONDS=21600
SOURCE_BLOCKED_RETRY_BASE_SECONDS=21600
SOURCE_BLOCKED_RETRY_MAX_SECONDS=86400
```

## 验收

```bash
pytest -q
python -m job_hub.cli source-run-ledger --date <YYYY-MM-DD>
```

服务器部署必须先备份 SQLite，再使用 `deploy/Dockerfile.phase90-fast` 和
`deploy/docker-compose.phase90-fast.yml` 滚动重建 `web` 和 `worker`。Caddy、域名和 DNS 不属于本阶段；IP 临时入口也不被标记为正式公网域名。
