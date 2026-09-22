# Phase 10：接入中国石油东方物探公开招聘页

## 阶段目标

本阶段把第一个已经逐项核验的中国石油体系下属单位正式招聘页接入生产采集链路。选择中国石油集团东方地球物理勘探有限责任公司（BGP），是因为其公开招聘页在本机 `direct` 模式下可以稳定取得，页面直接提供职位、地点、学历/专业要求和发布日期，且不需要登录、验证码或私有接口。

本阶段不把中国石油统一招聘平台、中国石化、国家管网或中国海油的访问故障改写成“无岗位”。这些来源仍按访问受限或结构待适配记录，学生端不会显示未经官方原文核验的岗位。

## 实现内容

- `structured_opening_page`：新增通用的结构化公开招聘页适配器。
- 标题/内容块严格一一配对；数量不一致时整页失败，避免岗位字段错配。
- 允许列表、robots、最终跳转域名和专业匹配沿用已有合规门禁。
- 结构化字段读取支持标签换行和 `Label: value` 两种形式。
- 每条岗位生成独立 `external_id`，保留官方原文和官方证据 URL。
- 新增 BGP 官方来源、组织层级频道和国家能源来源矩阵记录。
- 新增真实页面摘录夹具和适配器/契约回归测试。

## 真实官方来源

- 单位：中国石油集团东方地球物理勘探有限责任公司
- 官方招聘页：<https://www.bgp.com.cn/bgpen/Recruitment/first_common2023hr.shtml>
- 2026-09-22 本机直连观察：招聘页 HTTP `200`；`https://www.bgp.com.cn/robots.txt` HTTP `404`，按现有规则视为未设置可解析 robots 限制。
- 页面原文岗位：`Seismic Data Processing Geophysicist (Experienced)`
- 页面原文地点：`Saudi Aramco GDAD Office, Saudi Arabia`
- 页面原文要求：本科及以上，Geophysics、Exploration Geophysics、Petroleum Geology 或相关地学专业；硕士优先。
- 页面原文日期：`2026/6/27`。

该岗位是中国石油体系内的油气工程技术服务单位岗位，分类为“油气工程技术服务”，不再使用“油气上游业主”来描述 BGP。它属于海外岗位，不能代表国内岗位数量已经全面补齐。

## 运行方式

```powershell
$env:HTTP_TRANSPORT_MODE = "direct"
python -m job_hub.cli source-health --source-id cnpc-bgp-recruitment
python -m job_hub.cli sync-source cnpc-bgp-recruitment
python -m job_hub.cli coverage
```

服务器复测仍需在实际部署主机执行：

```bash
HTTP_TRANSPORT_MODE=direct \
python -m job_hub.cli national-entry-probe \
  --transport direct --environment production-server \
  --output /var/lib/job-hub/national-entry-probe-direct.json
```

当前没有服务器 SSH 或部署控制台，因此本阶段不能伪造服务器结果；本地直连证据和服务器复测命令均保留在报告中。

## 审阅入口

1. 查看 [BGP_SOURCE_EVIDENCE.md](BGP_SOURCE_EVIDENCE.md) 和真实页面摘录夹具。
2. 查看 [MIGRATION_NOTES.md](MIGRATION_NOTES.md)，确认没有 SQLite schema 迁移。
3. 查看 [QUALITY_REPORT.md](QUALITY_REPORT.md)，核对岗位字段、限制边界和测试结果。
4. 查看 [REFERENCE_TRANSFER.md](REFERENCE_TRANSFER.md)，核对参考项目的借鉴边界。
5. 审阅通过后再合并或开始下一家下属单位来源。
