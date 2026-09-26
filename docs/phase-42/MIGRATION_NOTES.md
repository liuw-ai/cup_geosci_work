# Phase 42 迁移说明

## 代码

- 新增 `job_hub.domain_probe.probe_public_domain`，只读解析 DNS 并访问 HTTPS 健康端点。
- 新增命令：

  ```bash
  python -m job_hub.cli domain-check \
    --hostname jobs.cupdky.cn --expected-ip 81.70.62.174
  ```

- 无数据库 schema 迁移；命令只输出诊断结果，不写入招聘岗位或覆盖来源状态。

## 部署

部署镜像后，先执行 `docker compose config`、`docker compose up -d --build web worker`
和 `docker compose -f docker-compose.public.yml up -d`。DNS 记录由域名服务商配置，
Caddy 会在解析成功后自动申请和续期证书。

## 回滚

恢复到 `v0.22.15` 即可移除本阶段 CLI；服务器数据库和岗位数据不受影响。域名配置
文件属于上一轮未改变的数据模型，回滚时仍应保留本机回环绑定和 Caddy 的安全边界。
