# Migration Notes

本阶段没有数据库迁移，也没有修改岗位发布、专业匹配或过期清退规则。

部署变更仅限 `deploy/Caddyfile.example`：

- `jobs.cupdky.cn` 保持为唯一正式 HTTPS 主机名；
- 在 DNS 未生效期间，`http://81.70.62.174` 可临时反向代理到 `web`；
- 临时 IP 地址不启用 TLS，不应写入学生通知、二维码或正式材料。

服务器更新配置后需要仅重建 Caddy 容器，不重建 `web`、`worker` 或 SQLite 数据卷：

```bash
docker compose -f docker-compose.public.yml up -d --force-recreate caddy
```
