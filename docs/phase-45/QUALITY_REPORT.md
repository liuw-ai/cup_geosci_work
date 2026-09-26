# Phase 45 质量报告

日期：2026-09-26  
分支：`phase/45-dns-and-source-sweep`

## 服务器运行基线

| 指标 | 结果 |
| --- | ---: |
| worker 心跳 | 正常（`ok=true`） |
| 数据库岗位记录 | 936 |
| 学生端当前公开岗位 | 201 |
| 登记来源 / 启用来源 | 73 / 38 |
| 数据审计 | `ok=true` |
| 政府台账当前明确匹配 | 2 |
| 政府台账匹配类型 | 安徽事业单位博士岗位 |

## 来源复测

| 来源 | 发现 | 当前匹配 | 结论 |
| --- | ---: | ---: | --- |
| 山东省地矿局 | 2 | 0 | 扫描成功，无当前地学匹配 |
| 河南省地质局 | 2 | 0 | 扫描成功，已排除过期/流程公告 |
| 湖北省自然资源厅 | 5 | 0 | 扫描成功，无当前匹配 |
| 湖南省地质院 | 2 | 0 | 扫描成功，无当前匹配 |
| 宁夏地质局 | 1 | 0 | 扫描成功，无当前匹配 |
| 安徽省地矿局 | 1 | 0 | 扫描成功；已由职位表台账发布 2 条明确岗位 |
| 中国石油浏览器捕获 | - | - | 官方列表 HTTP 412，访问受限 |
| 中国石化浏览器快照 | - | - | 捕获超过 30 小时，来源故障 |

“发现”是本次公开栏目解析到的公告数量，不是岗位数量。0 个当前匹配只在
扫描完成且没有过期/专业不匹配行时成立；访问受限来源不会被写成“无岗位”。

## 域名门禁

`jobs.cupdky.cn` 当前仍为 `NXDOMAIN`，因此 Caddy 不能申请域名证书。IP 地址
不是学生端正式地址；必须先由域名服务商添加 `jobs -> 81.70.62.174`，再通过
`domain-check` 和公网 HTTPS 检查。

## 验收命令与结果

```bash
docker compose exec -T web python -m job_hub.cli worker-health --max-age 300
docker compose exec -T web python -m job_hub.cli audit
docker compose exec -T web python -m job_hub.cli government-position-audit --today 2026-09-26
docker compose exec -T web python -m job_hub.cli domain-check \
  --hostname jobs.cupdky.cn --expected-ip 81.70.62.174
```

前三项通过；最后一项在 DNS 记录添加前按设计失败并返回 `nxdomain`。

## 真实性结论

本阶段没有用历史岗位或聚合页面增加数量。当前“全国大量国内岗位、三桶油下属单位、
事业编、公务员”仍未完成，原因是部分官方系统访问策略/动态接口尚未取得当前岗位
级证据；下一阶段必须继续逐来源补齐，而不是降低发布门禁。
