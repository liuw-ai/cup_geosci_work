# Phase 44：服务器官方来源扩展与域名上线门禁

本阶段把一次服务器直连健康扫描转成可维护的来源状态：山东省人事考试网省属事业单位栏目通过服务器的 robots 与正式入口检查，并保留原有解析配置、样例/夹具和备用入口。甘肃省地质矿产勘查开发局仅完成网络健康探测，因缺少岗位样例/回归夹具继续停用；其它“入口可达但尚未完成字段验证”的来源同样停用。

## 本阶段交付

- `shandong-hrss-exam`：山东省人力资源和社会保障厅正式省属事业单位招聘入口，服务器验证状态为 `server_health_and_adapter_verified`。
- `gansu-geology-bureau`：甘肃省地质矿产勘查开发局正式人事栏目已完成服务器健康探测，但因尚无岗位样例/回归夹具，继续停用并进入适配器验证队列。
- 新增 `server_health_and_adapter_verified` 数据契约状态，防止验证台账与契约枚举不一致。
- 省级目标矩阵与来源验证台账同步升级；来源故障、扫描成功无匹配和历史截止岗位仍严格区分。
- 域名上线仍由注册商 DNS 记录触发：`jobs.cupdky.cn` 必须解析到 `81.70.62.174`，Caddy 才能签发 HTTPS。

## 不在本阶段宣称

- 不把 39 个可达入口全部视为可发布来源；可达不等于有岗位，也不等于完成岗位级字段验证。
- 不导入历史山东、河南、天津职位表作为当前在招。
- 不绕过中石化、中石油或其他站点的 robots、验证码、登录或访问策略。

## 验收命令

```bash
docker compose exec worker python -m job_hub.cli worker-health --max-age 300
docker compose exec web python -m job_hub.cli audit
docker compose exec web python -m job_hub.cli source-health --include-disabled
docker compose exec web python -m job_hub.cli domain-check \
  --hostname jobs.cupdky.cn --expected-ip 81.70.62.174
```

域名检查在 DNS 未配置时必须明确返回 `nxdomain`，不能将 IP 访问或 Caddy 容器运行误报为域名已上线。
