# Phase 87 政府职位表运行复核统一

本阶段修复政府职位表审计命令与生产 worker 的状态分叉。

此前 worker 会读取数据库中的 `government_source_verifications`，依据当天官方公告/附件复核结果决定岗位是否继续发布；但管理员运行 `government-position-audit` 时只读取版本化职位表台账，可能在来源当天故障或被官方撤回后仍显示为可发布。现在审计命令也读取同一张数据库复核表，和生产发布使用同一门禁。

## 使用

```bash
docker compose exec -T worker python -m job_hub.cli government-position-audit \
  --today $(date +%F) \
  --max-age-hours 48 \
  --output /var/lib/job-hub/reports/government-position-audit-$(date +%F).json
```

报告中的 `source_evidence_verifications` 现在包含服务器最近一次复核状态：

- `verified`：官方公告和附件均可访问并通过证据检查；
- `source_unavailable`：暂时不可访问，保留最近成功时间，不立即伪造“无岗位”；
- `withdrawn`：官方明确撤回或取消，立即阻止发布；
- `not_configured`：来源未完成复核配置，不能发布其职位表行。

岗位仍必须同时满足官方岗位表字段、学生专业匹配、学历、地点、截止日期和来源新鲜度门禁。命令只读，不会自行启用来源或改变岗位。
