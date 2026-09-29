# Phase 79 服务器验收记录

检查时间：2026-09-29（Asia/Shanghai）。服务器已使用独立工作区
`/home/ubuntu/cup_geosci_work_phase79b` 切换 Web/Worker；原 Phase 78 和 Phase 79 工作区保留。

## 通过项

- 镜像构建成功，Web/Worker 均为 `healthy`。
- `audit`：岗位总数 1501，当前开放匹配 206，启用来源失败 0，stale crawl run 0。
- SLB 旧失败任务已重新运行并收敛为 `succeeded`，最新 `run_id=1369`；原失败抓取记录未删除。
- 数据库备份完整性通过，Worker 心跳正常。

## 未通过项

- `jobs.cupdky.cn` 仍为 `NXDOMAIN`，`domain-check.ready=false`，HTTPS 未检查。
- 因此 `production-readiness.public_ready=false`；不能将 IP 地址当作全院正式公网入口。

## 复核命令

本次服务器切换通过受控代码归档完成（服务器当时无法连接 GitHub），先备份数据库，再构建 Web/Worker 镜像并执行：

```bash
docker compose exec worker python -m job_hub.cli sync-source slb-career
docker compose exec worker python -m job_hub.cli source-tasks --due-only
docker compose exec worker python -m job_hub.cli audit
```

验收重点是 `slb-career` 的当前任务状态是否从旧 `failed` 收敛为 `succeeded`，以及原失败 `crawl_runs` 是否仍可审计。域名 DNS 不在本阶段擅自修改。
