# Phase 50 质量报告

## 扫描日期

2026-09-27，使用云服务器公网出口直连官方站点。

## 来源结果

| 来源 | 官方入口 | 结果 | 学生端岗位 |
| --- | --- | --- | ---: |
| 山东省 2026 年公务员招录 | [山东省人力资源和社会保障厅公告](https://hrss.shandong.gov.cn/rsks/articles/ch03577/202511/101d712c-d68b-4ef8-b240-9fd37c9819e5.shtml) | 报名 2025-11-07 至 2025-11-10，已截止 | 0 |
| 浙江省公务员考试录用专题 | [浙江省公务员考试录用专题](http://gwy.zjks.gov.cn/zjgwy/website/init.htm) | 可访问；当前可见招录/遴选入口无面向社会毕业生的开放地学职位表 | 0 |

## 台账审计

```text
government-position-audit --today 2026-09-27
source assessments: 12
government records: 136
civil-service records: 0
verified open records: 122
verified scan no current match: 3
```

本阶段没有增加“岗位总数”，因为两份公务员来源均没有满足当前开放、面向学生、专业明确和官方字段齐全的岗位。增加历史或不适用岗位会违反发布门禁。

## 质量结论

- 山东和浙江的官方入口已从未扫描状态推进到可审计扫描状态。
- 已截止、面向在编人员的遴选和缺少岗位级专业证据的内容均未进入学生端。
- 国家公务员年度职位表仍保持 `source_unavailable`，不使用历史职位表代替当年度数据。
