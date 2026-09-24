# Phase 17 质量报告

## 数据与门禁

| 指标 | 结果 |
| --- | ---: |
| 原始岗位记录 | 248 |
| 学生端公开在招 | 54 |
| 中国石油官方快照岗位 | 20 |
| 中国石油下属油田/油气田单位 | 10 |
| 100 人模拟明确匹配 | 100/100 |
| 明确岗位-画像匹配数 | 152 |
| 来源最高集中度 | 国家管网 21/54 = 38.89% |
| 质量门禁 | `pass` |

20 条中国石油快照岗位均满足：

- `source_id = cnpc-career`，官方详情页在 `zhaopin.cnpc.com.cn`；
- `field_evidence["岗位"]` 与 `title` 完全一致；
- 有岗位级专业、学历、地点、截止日期和招聘人数证据；
- 导入临时数据库后全部为 `student_eligible`。

## 自动化检查

```text
python -m pytest -q                         212 passed
python -m compileall -q job_hub tests        passed
git diff --check                             passed
python -m job_hub.cli audit                  ok: true, 248 checked, 54 open, 0 issues
python -m job_hub.cli coverage ...           quality_gate.status = pass
python -m job_hub.cli simulate-cohort        100 explicit matches, 0 review-only, 0 without recommendation
```

审计中的历史中国石油 HTTP 412 抓取失败记录继续保留，用于说明普通 HTTP 访问受限；它不覆盖当前浏览器核验快照，也不被解释为“无岗位”。

## 仍需关注

- CNPC 20 条岗位来自同一官方招聘系统，类别来源集中度仍需继续下降；中石化、中海油、自然资源/地调和公务员职位表尚未形成同等规模的当前岗位快照。
- `category_source_concentration` 中管网、CNPC 等类别仍可能单一来源占比高，后续按单位矩阵继续扩源。
- 中国海油集团动态页本次是可访问但“暂无数据”，队列标记为 `scan_success_no_match`；中海油服 ATS 仍为 `access_limited`。
