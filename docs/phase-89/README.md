# Phase 89：服务器运行账本部署

## 为什么做这一阶段

Phase 88 已在本地完成来源运行账本，但服务器仍运行旧的 Phase 42 工作区。容器健康并不代表运行的是当前代码，因此必须先完成生产切换，才能验证每日来源运行结论是否真正可用。

## 部署策略

服务器构建普通 Dockerfile 时，依赖下载速度过低，无法在合理时间内完成。为避免停止旧服务或留下半成品，本阶段提供 `deploy/Dockerfile.phase89-fast`：

- 复用服务器已有的 `cupb-geoscience-job-hub:latest` 依赖镜像；
- 只复制本阶段已审阅的 `job_hub` 和 `data`；
- 使用同一个 Compose project 和 `job_hub_data` volume；
- 不改 `.env`、数据库内容或 Caddy 配置；
- 正常 `Dockerfile` 仍是未来干净重建和镜像发布的标准路径。

这是一种部署链路降级，不是数据采集链路降级。它不改变 robots、访问策略、专业门禁或官方证据规则。

## 生产验收命令

```bash
docker compose -p cupb-geoscience-job-hub \
  -f docker-compose.yml \
  -f deploy/docker-compose.phase89-fast.yml \
  config --quiet

docker compose -p cupb-geoscience-job-hub \
  -f docker-compose.yml \
  -f deploy/docker-compose.phase89-fast.yml \
  up -d --no-build web worker

docker compose -p cupb-geoscience-job-hub \
  -f docker-compose.yml \
  -f deploy/docker-compose.phase89-fast.yml \
  exec -T web python -m job_hub.cli source-run-ledger --date <YYYY-MM-DD>
```

验收必须同时确认：

1. Web health 为 healthy；
2. Worker health 为 healthy；
3. `source-run-ledger` 能执行；
4. 数据库完整性检查为 `ok`；
5. 旧备份仍可读；
6. 失败来源没有被显示成“无岗位”。

## 回退

```bash
docker compose -p cupb-geoscience-job-hub -f docker-compose.yml up -d --no-build web worker
```

回退前后均不删除 `job_hub_data` volume。Phase 89 备份归档和 SQLite 副本必须保留。

