# Quality Report

## Evidence gate

| Check | Result |
|---|---|
| 官方双表页面解析 | 12 条岗位行 |
| 岗位行专业字段 | 每条重建记录都有 `field_evidence.专业范围` |
| 竞赛词污染 | 未进入任何重建岗位的 `match_text` |
| 原公告聚合记录 | `275` 已 superseded |
| 审计 | `ok: true` |

## Runtime baseline after migration

| Metric | Value |
|---|---:|
| Open jobs | 91 |
| Strong matches | 0 |
| Open records without field evidence | 83 |
| Cohort explicit matches | 0 students |
| Cohort review opportunities | 100 students |

“0 个明确匹配”是当前真实证据完整率不足的结果，不是“没有地学岗位”的结论。Halliburton、SLB 等岗位仍可作为需核验机会显示；只有重新取得岗位级专业/学历字段，才能升级为明确匹配。

## Remaining risk

当前仍有 83 条开放记录缺少岗位级证据，主要来自旧版来源适配器或外文职位详情。下一阶段应优先为这些来源补充详情字段或官方职位表适配器，而不是放宽专业门禁。
