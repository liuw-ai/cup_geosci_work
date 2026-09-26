# Phase 42：生产域名预检与官方来源巡检

本阶段把生产域名上线前的 DNS/HTTPS 依赖变成可重复检查的命令，并在服务器上
复测一批高价值官方来源。目标不是用历史岗位增加数量，而是让“域名未解析、证书
未签发、应用故障、来源无匹配、来源受限”分别可见。

## 已完成

- 新增 `job_hub.domain_probe` 和 `domain-check` CLI：检查 DNS 地址、期望 A 记录、
  HTTPS 健康端点和证书/HTTP 错误；失败时返回非零退出码。
- Caddy 生产配置、`APP_BASE_URL` 和应用端口保持 `jobs.cupdky.cn`、本机回环和
  `web:8080` 的一致性。
- 服务器复测（2026-09-26）：`ccgc-careers` 发现 18 条公告、`cgs-notices` 0 条、
  `cosl-career` 0 条、`cmgb-second-geology-recruitment` 1 条、
  `cmgb-inner-mongolia-geology-recruitment` 3 条、`cnpc-bgp-recruitment` 1 条、
  `beijing-hrss` 11 条；本轮没有新增当前地学专业匹配岗位，全部按现有门禁处理。
- 服务器 Caddy 日志确认当前域名阻塞是 Let's Encrypt 对 `jobs.cupdky.cn` 的
  `NXDOMAIN`，不是 Web 容器或 Caddy 配置错误。

## 明确的外部动作

域名服务商必须创建 `A` 记录：主机名 `jobs`，值 `81.70.62.174`。这一步需要域名
所有权凭据，不能由仓库代码或 SSH 服务器代替。记录传播后，再运行：

```bash
docker compose exec web python -m job_hub.cli domain-check
curl -I https://jobs.cupdky.cn/healthz
```

只有预检通过、HTTPS 返回成功后，才把域名作为学生端正式入口。

## 下一步扩源顺序

1. 服务器浏览器窗口允许时复测中石化 SPA、国家管网和中国石油岗位详情捕获；
2. 对中海油及中海油服的公开招聘入口继续保存列表/详情请求和字段证据；
3. 按最新官方公告登记山东、河南、天津及其他省份职位表，过期附件只保留历史证据；
4. 国家公务员年度职位表发布后，逐行导入职位代码、专业、学历、地区、人数和截止日；
5. 每次扩源都要运行 `audit`、`coverage --record` 和 `worker-health`，来源故障不得记为无岗位。
