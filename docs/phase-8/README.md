# Phase 8：国家能源体系公开入口探测

## 目标

本阶段为中国石油、中国石化、中国海油和国家管网四个体系建立“主招聘入口 + 备用官方入口”的可审计登记表，并增加只读入口探测器。探测器只回答入口是否能够在当前运行环境中被合规访问、是否存在公开招聘链接以及是否需要专用适配器；它不采集岗位，也不把入口可访问解释为有岗位。

## 交付范围

- `data/national_entry_targets.json`：四个体系的入口层级、官方域名白名单、招聘关键词和备用入口。
- `job_hub/entry_probes.py`：robots 检查、有限重试、跳转校验、HTML/动态壳/访问策略状态识别。
- `data/national_entry_probe_runs.json`：2026-09-22 在本地网络的真实只读探测记录。
- `job_hub/contracts.py`：入口目标和探测运行契约，确保失败记录可保存且不会进入岗位表。
- `python -m job_hub.cli national-entry-probe`：管理员 CLI。
- `GET /api/admin/national-entry-probes`：受 `X-Admin-Token` 保护的管理员接口。
- `tests/test_entry_probes.py`：10 个离线回归测试，覆盖备用入口、robots、动态壳、契约一致性、私有接口和 CLI 输出。

## 探测边界

每个入口按以下顺序处理：读取 `robots.txt`，在允许的情况下请求登记 URL，检查最终主机是否仍属于官方白名单，再提取有限数量的公开链接。只对临时网络错误和指定临时 HTTP 状态执行有限指数退避；不使用登录、验证码绕过、浏览器指纹伪装、代理轮换或申请接口。

入口状态与岗位状态严格分离：

| 探测分类 | 含义 | 能否产生岗位 |
| --- | --- | --- |
| `accessible_html` | 公开 HTML 可读取 | 不能，仍需岗位字段适配和原文审计 |
| `accessible_dynamic_shell` | 页面可读取但只有动态壳 | 不能，需找到合规公开数据接口或人工核验 |
| `access_policy_block` | HTTP 401/403/412/429 等访问策略响应 | 不能，记为访问受限 |
| `robots_blocked` | robots 明确禁止该入口 | 不能 |
| `source_unavailable` | TLS、超时或其他网络/服务器故障 | 不能，不能解释为无岗位 |
| `soft_not_found` | 入口返回 404 | 不能，需重新定位栏目 |
| `redirected_outside_entry` | 跳转到未登记官方主机 | 不能，需人工确认 |
| `non_html` | 返回非 HTML 内容 | 不能，需附件或专用处理链路 |

只有独立的岗位适配器同时取得岗位标题、专业/学历/地点/截止日期证据和官方原文链接后，才可以进入学生端岗位库。

## 真实探测记录

运行日期：`2026-09-22`；环境：`local-network`；体系数：4；入口尝试数：8（每个体系 1 个主入口和 1 个备用入口）。

| 体系 | 主/备用入口结果 | 体系结论 | 学生端岗位变化 |
| --- | --- | --- | --- |
| 中国石油 | 两个入口均为 TLS EOF，无法完成 robots 验证 | `source_unavailable` | 0 |
| 中国石化 | 两个入口均为 TLS EOF，无法完成 robots 验证 | `source_unavailable` | 0 |
| 中国海油 | 招聘页 HTTP 200 但为动态壳；集团官网 HTTP 200，发现公告栏目 | `accessible_structure_unverified` | 0 |
| 国家管网 | 两个入口均为 TLS EOF，无法完成 robots 验证 | `source_unavailable` | 0 |

中国海油页面发现的官方公告栏目为 [`https://www.cnooc.com.cn/zxzx/ggxx/`](https://www.cnooc.com.cn/zxzx/ggxx/)。这只是下一步核验目标，不是已经验证的岗位来源；当前没有将公告标题或动态壳内容写入学生端。

## 参考项目的采用边界

- 借鉴 JustHireMe 的来源适配器分层、有限重试和失败可追踪思想。
- 借鉴 WeHireMonitor 的成功、失败、待复核状态机表达。
- 未采用 `curl_cffi`、Firecrawl 或 Scrapling 去绕过访问策略。它们不能替代官方授权、robots 规则和岗位证据；如后续某站点允许浏览器渲染，也必须单独记录合规依据和回归夹具。

## 运维入口

```powershell
python -m job_hub.cli national-entry-probe
python -m job_hub.cli national-entry-probe --system cnooc --output .\runtime\cnooc-entry-probe.json
```

管理员可请求 `GET /api/admin/national-entry-probes` 查看目标登记、最近运行记录和汇总。学生端不暴露备用 URL、错误堆栈或内部探测结果。

## 下一步

1. 在长期在线服务器网络重新探测中国石油、中国石化和国家管网，区分本地网络 TLS 故障与站点访问策略。
2. 对中国海油公告栏目逐页核验列表、详情、附件和岗位字段，形成专用公告适配器或人工核验队列。
3. 对任何可用入口先完成离线夹具、字段完整率和原文域名审计，再考虑启用来源；在此之前不增加“无岗位”结论。
