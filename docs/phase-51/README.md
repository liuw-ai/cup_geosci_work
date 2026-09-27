# Phase 51：中冶地质总局国聘地学岗位核验桥接

## 目标

把中国冶金地质总局 2027 届校园招聘公告及其官方国聘岗位详情接入学生端发布门禁，增加真实的中央地勘单位岗位，同时保留动态系统尚未完成全量自动导出的事实。

## 官方证据

- 总局公告：<https://www.cmgb.com.cn/content/2026/09-25/7509048960149884928.html>
- 官方招聘系统：<https://cmgb.iguopin.com/jobCampus>
- 岗位详情域名：<https://www.iguopin.com/job/detail>

## 本阶段变更

- 新增 `cmgb-iguopin-2027-geoscience-snapshot` 官方快照来源。
- 新增 `data/verified/cmgb-iguopin-geoscience-20260926.json`，保存 12 条逐岗位详情证据。
- 每条记录保留岗位详情 URL、单位、地点、学历、招聘人数、截止日期和专业原文。
- 仅允许 `official_detail_block` 岗位级证据进入专业发布门禁。
- 9 条明确匹配学生专业；2 条因“地质类相关”未列出具体目标专业，留在待核验；1 条本科岗位仅要求地质工程，不覆盖本科资源勘查工程，按专业门禁拒绝发布。

## 诚实边界

本阶段是动态国聘系统的人工核验桥接，不声称已导出全部 8 页岗位。未核验分页不被解释为“无岗位”，也不使用聚合平台或历史岗位补数。后续应在服务器浏览器环境完成分页扫描、逐岗位详情抓取和失败队列记录，再把快照替换为自动捕获清单。

## 验收

- `tests/test_official_snapshot_rows.py` 新增 CMGB 快照回归测试。
- 全量测试：296 passed。
- Python 编译检查通过。
- `docker compose config` 通过。

