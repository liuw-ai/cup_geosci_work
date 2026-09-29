# Phase 77 服务器验收记录

检查时间：2026-09-29（Asia/Shanghai）

## 已通过

- 新工作区：`/home/ubuntu/cup_geosci_work_phase77`；旧工作区仍保留。
- Web、Worker：健康运行，Worker 心跳小于 5 分钟。
- 数据库审计：`1478` 条岗位，`206` 条当前开放匹配岗位，`enabled_source_failures=0`，无 stale crawl run。
- SLB 官方同步：`discovered=8`、`open_matches=4`、`created=1`、`updated=1`。
- 中石化官方浏览器快照：`132` 个单位、`35` 个地质候选单位、`363` 条岗位行，候选详情 `35/35`，失败岗位 `0`。
- 数据库备份：存在且 SQLite integrity 为 `ok`。
- 政府公告发现：`12` 个配置来源完成扫描，`53` 个官方附件登记；新附件仍停留在私有待复核队列。

## 未通过

- `jobs.cupdky.cn` DNS 检查为 `NXDOMAIN`，因此 HTTPS 未检查，公网正式发布门禁仍为 `public_ready=false`。

在域名服务商添加 `jobs` 的 A 记录指向 `81.70.62.174`、等待 DNS 生效并确认 Caddy 证书后，重新执行：

```bash
docker compose exec -T web python -m job_hub.cli domain-check
docker compose exec -T web python -m job_hub.cli production-readiness
```

在此之前不能向全院学生宣称正式公网版已经可用；IP 直连正常不等于域名 HTTPS 正式发布完成。
