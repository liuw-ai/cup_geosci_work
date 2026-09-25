# Phase 30：公网部署基线

本阶段将服务器部署从“仅本机回环可访问”改为可配置的公网绑定，同时保留本地开发的安全默认值。

## 配置

Compose 新增两个非敏感配置项：

```dotenv
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
```

服务器临时使用公网 IP 访问时，才设置为：

```dotenv
WEB_BIND_ADDRESS=0.0.0.0
WEB_PORT=80
APP_BASE_URL=http://服务器公网IP
```

云安全组必须允许 TCP/80；SSH 仍只用于管理员运维。正式长期使用应绑定域名、配置 HTTPS 反向代理，并将 `APP_BASE_URL` 改为 HTTPS 域名。

## 验收

1. `docker compose config` 成功且没有未替换变量。
2. `docker compose ps` 中 Web、Worker 均为 healthy。
3. 服务器本机 `/healthz` 返回 HTTP 200。
4. 服务器外部访问 `http://公网IP/healthz` 返回 HTTP 200。
5. 首页、岗位详情和移动视口均能返回，不把健康检查成功误认为公网访问成功。

本阶段不改变岗位筛选、来源证据或采集范围；公网可达不代表数据采集已经扩容完成。
