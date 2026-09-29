# Phase 72：浏览器 CDP 启动可靠性

## 目标

修复浏览器 Worker 与 `chromedp/headless-shell` 启动时的两个运行时问题：

1. Compose 不再向镜像重复传入 `9222` 调试端口，避免与镜像内置的 `9223 -> 9222` 转发冲突；
2. Worker 连接 CDP 时使用有界重试，避免浏览器刚启动的短暂连接断开被记录为长期来源故障。

重试只针对内部 CDP 就绪，不访问、绕过或放宽官方招聘平台的 robots、403、412、验证码和访问策略。

## 交付

- `docker-compose.browser.yml` 保留两个 CDP 端点为内部网络地址，不映射到公网；
- `_resolve_cdp_websocket` 最多重试 6 次，每次间隔 2 秒；
- 连接耗尽后仍写入失败捕获和 degraded 心跳，不生成“无岗位”；
- 增加瞬时断开后恢复的回归测试。

## 验收

本阶段完成前必须通过：

```text
python -m pytest -q
docker compose -f docker-compose.browser.yml config --quiet
```

服务器部署后还需确认两个 `/json/version` 返回 200，并检查 Worker 日志没有 `RemoteDisconnected` 或 `Connection refused`。
