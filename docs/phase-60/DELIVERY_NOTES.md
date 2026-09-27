# Delivery Notes

- 分支：`phase/60-public-domain-and-government-rescan`
- 本地回退点：将创建标签 `phase-60-review`
- 代码范围：Caddy 主机配置与本阶段交付记录
- 数据库：无迁移；既有 SQLite 卷保持不变

回退 Caddy 配置时，恢复上一版 `deploy/Caddyfile.example` 并仅重建 `caddy` 服务即可。岗位数据库与采集服务不受该回退影响。

未完成项：`jobs.cupdky.cn` 必须由域名所有者启用 DNS 托管和 A 记录；当年度公务员职位表及更多省级事业单位职位表仍需逐个官方来源接入。
