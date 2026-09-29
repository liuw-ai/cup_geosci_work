# Phase 78 服务器验收记录

检查时间：2026-09-29（Asia/Shanghai）

## 通过项

- 新工作区：`/home/ubuntu/cup_geosci_work_phase78`；旧工作区保留。
- 阶段 78 Web/Worker 镜像构建成功，数据库未迁移、未清空。
- 岗位总数：`1501`；审计开放匹配岗位：`206`。
- `enabled_source_failures=0`，无 stale crawl run。
- 附件待复核候选：`0`；与已发布官方台账重复的两个甘肃解析候选已拒绝并保留原始附件证据。
- 已发布甘肃岗位记录的 `field_evidence` 保留招聘人数、职位代码、官方公告和附件 URL。
- 数据库备份完整性：`ok`；Worker 心跳正常并继续执行当轮来源同步。

## 未通过

- `jobs.cupdky.cn` 仍为 `NXDOMAIN`，因此 HTTPS 未检查，`public_ready=false`。

必须在域名服务商添加 `jobs -> 81.70.62.174` 的 A 记录，等待解析生效后再执行 `domain-check` 和 `production-readiness`。在此之前，不能把 IP 临时入口当作全院正式公网地址。
