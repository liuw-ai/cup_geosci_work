# Phase 3: 组织层级与正式入口矩阵

## 目的

Phase 3 建立长期扩源所需的第二本注册表：`data/organization_registry.json`。它回答“是谁、属于什么体系、正式招聘入口在哪里、当前是否有经过验证的来源绑定”，而不是回答“今天收到了多少岗位”。

该设计服务于中国石油大学（北京）地球科学学院的七种固定匿名画像：资源勘查工程本科；地质学、地质工程、地质资源与地质工程的硕士和博士。学生端仍只看到通过官方原文核验的具体岗位，不会看到内部组织台账、候选入口或第三方线索。

## 关键区分

| 概念 | 保存位置 | 用途 | 不能代表什么 |
| --- | --- | --- | --- |
| 单位别名与岗位归一化 | `data/employer_registry.json` | 将公告里的单位名映射到可解释的就业路径 | 不是完整组织目录，也不提供采集入口 |
| 组织层级与正式频道 | `data/organization_registry.json` | 登记集团、子单位、产业角色、校招/社招/公告入口和备用入口 | 不是岗位库，也不表示该入口已经可抓取 |
| 来源采集配置 | `data/sources.json`、`data/provincial_sources.json` | 定义可被 worker 检查和采集的来源适配器 | 启用前仍需 robots、页面结构和样例核验 |
| 31 省扩源目标 | `data/source_targets.json` | 记录五类官方角色的核验进度 | 候选或待定位不代表该省没有招聘 |

“三桶油”和油服在本阶段不再使用含混的并列分类：

- 中国石油、中国石化、中国海油及其油田/区域公司属于上游勘探开发运营体系。
- 勘探开发研究院等属于集团内研究机构。
- 东方物探、中油测井、川庆钻探、长城钻探、渤海钻探、西部钻探、石化油服、中海油服、海油发展属于各集团体系内技术服务单位。
- 杰瑞、安东、中曼属于独立或民营油服。
- SLB、Halliburton、Baker Hughes、Weatherford 属于国际油服；是否有中国境内机会必须以岗位原文地点为准。
- 国家管网、自然资源/地调/地勘、矿业、石油与地学高校、政府招聘系统单独建模，不被误归为油服。

## 数据模型

```text
organization_registry.json
  organization
    id / canonical_name / aliases / official_domains
    parent_id ------------------------------> 集团或上级组织
    organization_role ----------------------> 上游运营、研究院、集团内技术服务等
    channels[]
      official_url + backup_urls
      verification_status
      source_id ----------------------------> sources.json / provincial_sources.json

source_id + enabled + source_health + latest crawl_run
  -> 决定某个 automation_ready 频道是否真的可进入自动化流程
  -> 不改变学生端只展示已发布官方岗位的规则
```

频道状态有严格含义：

| 状态 | 含义 | 会不会被 worker 自动采集 |
| --- | --- | --- |
| `automation_ready` | 频道已绑定来源适配器；运行时仍须健康检查和成功抓取 | 只有绑定来源已启用且运行条件满足时才可能 |
| `official_confirmed` | 单位与正式入口身份已登记 | 不会 |
| `candidate` | 可能的正式入口，尚未完成身份或结构核验 | 不会 |
| `blocked` | 公开访问条件不允许或存在访问控制 | 不会 |
| `unlocated` | 尚未找到稳定正式入口 | 不会 |

`backup_urls` 只表示“登记了另一个官方入口”，不是已经通过运行验证的故障切换。真正可用的备用来源仍要看来源健康、最近抓取运行和 `fallback_source_ids`。

## 本阶段种子范围

注册表当前包含 45 个组织、63 个官方频道。它覆盖中国石油、中国石化、中国海油、国家管网、集团内研究院与技术服务单位、独立/国际油服、中央地勘与地调、矿业、重点石油/地学高校、自然资源部和北京人社官方入口等优先对象。

其中 12 个频道标为 `automation_ready`，51 个为 `official_confirmed`。这不是“12 个全部运行成功的来源”，更不是“63 个已采集渠道”；数字只反映 Phase 3 的登记和绑定状态。三桶油的统一招聘入口仍保留为已确认但待稳定自动化验证的频道，符合“不能绕过 robots、登录、验证码、TLS 或动态访问控制”的项目边界。

这也是一个种子矩阵，不是对上千个三桶油子单位、31 省五类来源或全部高校的完成宣称。后续阶段会按可核验的单位/省份批次扩充，并为每个入口保存样例、字段验证和回归测试。

## 管理与查看

完整明细只对管理员开放：

```powershell
python -m job_hub.cli organization-matrix
python -m job_hub.cli organization-matrix --organization-role internal_technical_service
python -m job_hub.cli organization-matrix --output .\runtime\organization-matrix.json
```

HTTP 管理接口：

```text
GET /api/admin/organization-matrix
Header: X-Admin-Token: <ADMIN_TOKEN>
```

可选查询参数：

- `organization_role`：例如 `upstream_operator`、`internal_technical_service`。
- `affiliation`：按精确所属体系筛选，例如 `中国石油体系`。

接口返回组织层级、频道、备用入口、来源绑定、来源启用状态、最新来源健康状态和最近抓取状态。无令牌请求返回 `403`。公开 `/api/coverage` 只保留组织数、频道数、角色分布、状态分布和聚合准备度指标，不暴露频道 URL、来源绑定或核验备注。

## 本阶段不做的事

- 不批量访问、绕过或强行接入中国石油、中国石化、中国海油、国家管网等动态招聘站。
- 不将组织注册数、入口数或 `official_confirmed` 数量写成岗位数。
- 不新增、伪造或从第三方聚合页直接发布真实岗位。
- 不改变学生端页面布局、岗位查询行为或公开数据边界。
- 不替代下一阶段的 31 省五类官方来源逐站核验。

完整交付边界见 [MIGRATION_NOTES.md](MIGRATION_NOTES.md)、[QUALITY_REPORT.md](QUALITY_REPORT.md) 和 [DELIVERY_NOTES.md](DELIVERY_NOTES.md)。
