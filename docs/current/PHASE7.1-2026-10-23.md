# Phase 7.1 生产就绪审查

审查日期：2026-10-23（Asia/Shanghai）  
当前代码基线：`0e6991380901df9d6e27fd9a56f61979ede0243c`  
当前服务器 release：`phase7-20261023-r3`

## 逐项核对

| 项目 | 结果 | 证据或剩余动作 |
| --- | --- | --- |
| 隐私访问统计 | 通过 | 总访问量、独立访客、页面访问量和按日趋势已实现；仅保存按日 HMAC 摘要和聚合值；管理员、API、静态资源、健康检查和可识别机器人排除。专项回归覆盖 `tests/test_privacy_analytics.py`。 |
| 管理员网页登录 | 通过 | 用户名 + Werkzeug 密码哈希 + HttpOnly/SameSite 会话 + CSRF；页面不接受 `ADMIN_TOKEN` 登录。角色为 `admin`、`reviewer`、`analyst`。 |
| 机器 API 凭据 | 通过 | `ADMIN_TOKEN` 仅用于受保护机器 API；匿名统计 API 返回 403，Token API 已验证 200。 |
| 管理员工作台 | 通过 | 审核队列、来源漏斗、三桶油入口证据、审核事件和访问统计均在登录及权限检查后提供，并设置 `Cache-Control: no-store`。 |
| 自动更新 | 通过 | Worker 常驻运行，按来源周期同步；20:00 执行同步、过期清理、审计、日报冻结与通知；失败重试和备份失败阻断已实现。 |
| 数据安全边界 | 通过 | 当前 release 使用固定镜像摘要；数据库审计 `4438` 条记录、`749` 条当前公开岗位、`issues=[]`；最近备份完整性通过。 |
| Caddy 反向代理 | 代码已修正 | 正式域名代理到共享网络 `web:8080`；IP HTTP 入口改为 308 重定向到正式 HTTPS，不再代理明文管理员登录。服务器 Caddy 更新需随下一次部署执行。 |
| DNS | 已解析，待 HTTPS | `cupgeojob.com` 和 `www.cupgeojob.com` 已解析到 `81.70.62.174`；服务器 Caddy 尚未切换到新域名。 |
| HTTPS | 服务器本机通过，公网待放行 | Caddy 已切换并成功签发 Let’s Encrypt 证书（`cupgeojob.com`、`www.cupgeojob.com`）；服务器本机 SNI `/healthz` 返回 200，外部网络访问 443 仍超时，需检查云平台安全组/公网入站策略。 |
| 来源新鲜度 | 未通过，需真实捕获 | 当前门禁报告包含 1 个无成功捕获证据来源和 8 个过期来源。不能修改 `max_age_hours` 或用 Worker 心跳冒充捕获。 |
| 政府职位台账 | 未通过，需真实复核 | 台账 `as_of=2026-09-27`，年龄约 624 小时，配置上限 48 小时；必须成功复核或按失败保留待审，不能解释为无岗位。 |
| 20:00 日报闭环 | 待新周期证据 | Worker 正在运行，但本次审查尚未等待一个新的 20:00 周期完成；需核对 `worker_runs`、日报日期、delivery 状态和备份时间。 |
| 管理员密码轮换 | 必须上线前完成 | 旧初始密码曾在开发沟通中出现；正式 DNS/HTTPS 验收后必须生成新哈希、更新服务器 secret、重启 `web` 并重新验证登录。 |

## 服务器实况

- Web 仅监听 `127.0.0.1:8080`，Caddy 监听 80/443；应用和 Worker 容器健康。
- Caddy 使用共享 Docker 网络中的 `web:8080`，代码已切换正式站点为 `cupgeojob.com`，`www` 重定向到裸域。
- 下一次服务器切换必须设置 `APP_BASE_URL=https://cupgeojob.com`，届时会启用 Secure Cookie。
- `/healthz` 在服务器本机 HTTPS SNI 下返回 200，`/readinessz` 当前返回未就绪；这两个结果含义不同，不能用存活替代生产就绪。公网 443 尚未从外部网络验证通过。
- `/opt/job-hub/current` 仍是历史 phase4 符号链接；当前容器由 `/opt/job-hub/releases/phase7-20261023-r3/app/deploy/compose.yaml` 及对应 release env 管理。后续更新必须使用 release 专属路径，不要执行旧符号链接下的 Compose。

## 结论

代码、域名解析、Caddy、证书和应用入口切换已完成；正式公网长期运行尚未通过生产门禁。当前必须先在云平台放行 TCP 443，再从外部网络验证 HTTPS，随后完成真实来源/政府复核和新的 20:00 日报证据，再执行 `production-readiness --domain-hostname cupgeojob.com --require-public`。只有该命令返回 `internal_ready=true` 且 `public_ready=true`，才可以宣布正式公网版本。
