# Phase 30 质量报告

日期：2026-09-25  
分支：`phase/30-public-deployment`

## 代码验证

```text
python -m pytest -q
246 passed

python -m compileall -q job_hub
passed
```

## 公网部署验证

部署前服务器仅监听 `127.0.0.1:8080`，外部端口 80/8080 均不可达。完成 Compose 配置和公网绑定后，已通过不使用本机代理的外部直连复测：

```text
公网 URL：http://81.70.62.174/
/healthz：HTTP 200
首页：HTTP 200，19493 bytes
岗位详情 /jobs/494：HTTP 200，6595 bytes
移动端 User-Agent：HTTP 200，页面包含 viewport 元标签
web：healthy
worker：healthy
```

服务器监听：`0.0.0.0:80 -> container:8080`。公网 IP 访问已经成功；这仍然是 HTTP 临时入口，不等同于正式 HTTPS 部署。

## 数据与调度基线

```text
历史岗位：521
学生端在招：124
登记来源：72
启用来源：35
审计：ok=true，issues=[]
启用来源故障：1（中国煤炭地质总局旧入口 HTTP 404，已单独记录）
```

服务器 `.env` 保留 `DAILY_PUBLISH_TIME=20:00` 和 `SOURCE_SYNC_INTERVAL_MINUTES=180`；公网可达不改变每日 20:00 日报发布和定时同步机制。
