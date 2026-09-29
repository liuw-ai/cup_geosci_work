# Phase 88：官方来源每日运行账本

本阶段的目标不是增加虚构岗位，而是让每天的官方来源同步具备可审计的运行结论。现有 `crawl_runs` 只能表示 `finished/failed/skipped`，管理员无法稳定区分“完成扫描但无专业匹配”和“访问受限导致没有扫描”。

## 本阶段完成内容

- 为 `crawl_runs` 增加向后兼容的运行指标：统一结论、请求尝试次数、可重试失败次数、传输模式、证据完整岗位数、待人工复核数、附件解析成功数和非敏感元数据。
- 新增 `job_hub.run_ledger`，统一输出：
  - `success_with_matches`
  - `success_without_matches`
  - `source_unavailable`
  - `access_limited`
  - `parse_failed`
  - `manual_review_required`
  - `running`
  - `not_run`（已启用来源当日没有完成运行，不能解释为无岗位）
- 只有完整结束的扫描才能得到 `success_without_matches`；访问受限、TLS/网络失败、解析失败和运行中的任务不能解释为“无岗位”。
- `RequestPolicy` 累计记录一次来源运行中的请求次数、尝试次数和可重试失败次数，不记录 Cookie、正文或凭据。
- 附件队列处理后，将每个来源当前已提取和失败的官方附件数量回写到该来源最近一次运行记录。
- 每日日报增加 `source_runs` 账本，并在启用邮件时带出来源成功、无匹配、受限、不可用和解析失败计数。
- 新增 CLI：

```bash
python -m job_hub.cli source-run-ledger --date 2026-09-29
```

## 参考经验的实际迁移

- WeHireMonitor 的状态机思想：把失败、待复核和成功空结果分成不可混淆的状态。
- JustHireMe 的 live smoke/checklist 思想：账本输出可作为服务器每日验收的固定命令，而不是只看容器是否存活。
- Scrapy AutoThrottle/Retry-After 的可观测性思想：先记录重试和传输结果；本阶段不改变访问权限，也不绕过 robots 或验证码。
- Playwright 网络文档的捕获与发布分离原则：浏览器捕获成功仍需进入正常证据和发布门禁，不能直接当成完整扫描。

## 不在本阶段解决的事项

- 不会自动突破中国石油、中国石化或地方站点的访问策略。
- 不会把第三方聚合页直接发布给学生。
- 不会凭空补齐 31 省矩阵、当年度公务员职位表或三桶油下属单位岗位。
- `jobs.cupdky.cn` 的 DNS/HTTPS 仍需域名服务商配置，账本不改变公网域名状态。
