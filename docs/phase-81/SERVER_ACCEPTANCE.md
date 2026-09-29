# Phase 81 服务器验收

验收日期：2026-09-29（Asia/Shanghai）

部署方式：保留 Phase 79 数据库备份和 Docker 数据卷，在 `/home/ubuntu/cup_geosci_work_phase81` 使用独立工作区构建并启动 `web`、`worker`。未替换 `.env`，未清理浏览器 worker 或数据库卷。

## 结果

- `web`：healthy
- `worker`：healthy，`worker-health --max-age 600`：`ok=true`
- 容器内 `python-docx`：`1.1.2`
- `process-pending-artifacts --limit 500`：`selected=0`、`failed=0`。此前 Worker 已完成本轮登记队列处理。
- 官方附件台账：60 个；`extracted=42`、`skipped=18`，无 `registered` 或 `downloaded` 遗留项。
- 官方附件候选仍为私有复核状态；未因批量处理直接增加学生端岗位。
- `audit`：`ok=true`，`checked_jobs=1501`，`open_jobs=206`，`enabled_source_failures=[]`。
- 配置化政府公告发现：12 个来源、38 个公告、53 个附件登记、8 个岗位表附件、`failed=0`、`blocked=0`。
- Worker 完整同步后：政府来源证据复核 `verified=7`，附件处理 `failed=0`，覆盖快照已记录。

## 仍未通过的外部门禁

`jobs.cupdky.cn` 的 DNS/HTTPS 仍取决于域名服务商，不能因服务器 IP 可访问而标记为正式公网域名；在添加 `A jobs -> 81.70.62.174` 前，`public_ready` 仍保持 false。

本阶段也没有虚增岗位数量。只有管理员完成岗位级专业、学历、地点、人数、截止日期和官方证据复核后，候选才可进入学生端。
