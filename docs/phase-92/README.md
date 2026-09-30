# Phase 92: Verified CEA Position-Ledger Expansion

本阶段补齐中国地震局 2027 年事业单位公开招聘中经岗位级专业、学历与地点门禁确认的缺失职位行，并修正全国性公告在省份筛选中的归属。

## 完成内容

- 中国地震局官方公告和官方 XLSX 的人工核验台账由 70 条、89 个计划名额扩展为 91 条、112 个计划名额。
- 每条新增记录保留官方公告 URL、官方附件 URL、Excel 行号、岗位代码、单位、专业、学历、人数和地点。
- 为批量职位行增加可选的行级省份字段；郑州岗位不再错误归入北京筛选。
- CEA 自动下载受 `robots.txt` 限制，来源恢复为 `manual` 且禁用自动采集；每日仅执行允许的官方证据复核。人工核验台账继续受报名开始日、截止日与来源新鲜度门禁约束。

## 未纳入学生端

本次从 45 条宽泛地学候选中仅新增 21 条。其余 24 条只给出地球物理、遥感、测绘、岩土或电子等相邻条件，未直接覆盖当前地球科学学院七个公开学生画像，不能以“地学相关”理由发布给学生端。

## 验收

```text
PYTHONPATH=. pytest -q tests/test_government_positions.py tests/test_sources.py tests/test_government_artifacts.py
python -m compileall -q job_hub tests
```
