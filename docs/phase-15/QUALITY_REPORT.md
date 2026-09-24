# Quality Report

## Evidence gate

| Check | Result |
|---|---|
| 官方双表页面解析 | 12 条岗位行 |
| 岗位行专业字段 | 每条重建记录都有 `field_evidence.专业范围` |
| 竞赛词污染 | 未进入任何重建岗位的 `match_text` |
| 原公告聚合记录 | `275` 已 superseded |
| 国家管网官方地质岗位快照 | 24 条岗位行；21 条通过学生端门禁 |
| CUPB 官方公告核验快照 | 4 条岗位行；4 条通过学生端门禁 |
| 审计 | `ok: true` |

## Runtime baseline after migration

| Metric | Value |
|---|---:|
| Re-audited records after source expansion | 226 |
| Student-visible open jobs | 30 |
| Records pending job-level evidence | 159 |
| Explicit but non-target / experienced records | 36 |
| Student-visible target-major jobs | 25 |
| Student-visible unrestricted-major jobs | 1 |
| 100 人模拟中有明确匹配 | 100 人 |

当前 30 条公开在招岗位全部通过岗位级门禁，其中 21 条来自国家管网官方校园招聘系统地质关键词快照，4 条来自中国石油大学（北京）就业网既有来源，4 条来自 CUPB 新核验快照，1 条来自紫金矿业官方 Moka 招聘门户。CUPB 新快照中的 3 条明确地学岗位和 1 条“不限专业”岗位均保留了官方原文与岗位级字段。其余岗位不能因为属于能源、矿业或地勘行业而公开；只有重新取得岗位级专业、学历及学生可报条件，才能升级为公开岗位。

## Remaining risk

当前仍有 159 条记录缺少岗位级证据，主要来自旧版来源适配器、公告级页面或外文职位详情。公开岗位来源集中度为 70%（国家管网），质量门禁仍标记为 `needs_attention`；这表示真实岗位数量增加了，但来源多样性尚未达标。下一阶段应优先接入三桶油下属单位和事业编/公务员职位表，而不是放宽专业门禁。

## Publication gate correction

`相关机会` 不再属于学生端发布资格。2026-09-24 重审后，所有已有岗位行的炼化、化工、机械、油气储运、信息安全、财务、法律和明确多年经验岗位均被隔离；其余没有岗位级字段的记录进入 `pending_evidence`。学生端不再以行业相关性代替学生专业资格。国家管网快照中 21 条具备目标专业字段的岗位通过门禁，3 条仅岩土工程方向被隔离。

## 手机端检查

在本地学生端 `/jobs` 和 `/jobs/342` 检查了窄屏响应式 DOM：专业筛选、全国/省份筛选、岗位卡片和官方原文入口均存在；隔离岗位 `/jobs/339` 返回 HTTP 404。当前浏览器运行环境不接受显式 390px 视口覆盖，因此未把默认桌面视口数值误报为手机像素测试；仓库已有 Phase 9 的 390px 截图基线，服务器部署前仍应在真实微信/手机浏览器复测。
