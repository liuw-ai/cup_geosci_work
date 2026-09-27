# Phase 52：国聘校园招聘分页核验与字段完整性门禁

## 目标

在不降低地球科学学院专业门禁的前提下，完成中国冶金地质总局 2027 届国聘校园招聘列表的分页核验。该阶段的验收对象是“官方详情证据是否完整、分页是否完整、错配岗位是否被拦截”，不是单纯增加岗位总数。

## 官方范围

- 总局公告：[中国冶金地质总局 2027 届校园招聘公告](https://www.cmgb.com.cn/content/2026/09-25/7509048960149884928.html)
- 官方校园招聘列表：[国聘校园招聘](https://cmgb.iguopin.com/jobCampus)
- 官方岗位详情：[国聘岗位详情](https://www.iguopin.com/job/detail)

## 本阶段变更

- 以浏览器环境逐页扫描 8 页列表，共发现 146 条详情，146/146 条详情成功打开。
- 新增 `data/verified/cmgb-iguopin-geoscience-20260927.json`，保存 33 条具有岗位级地学证据的候选记录。
- 兼容动态详情页中 `text` 与 `generic` 两类字段节点，确保招聘人数、学历、地点、专业和截止日期不会因节点类型变化而丢失。
- 通过学生端发布门禁后：21 条 `student_eligible`、9 条 `pending_evidence`、3 条 `out_of_scope`。
- 回归测试固定分页快照数量和字段完整性，防止后续采集器静默退化。

## 明确边界

本阶段是一次可回溯的官方浏览器核验快照，不是服务器自动实时采集器。未进入快照的 113 条列表记录不能解释为“无岗位”；详情访问失败、动态接口变化和后续新增岗位仍须由浏览器 worker 处理。`pending_evidence` 不公开给学生端，只有明确专业和学历证据的记录才发布。

## 验收命令

```bash
python -m pytest -q tests/test_official_snapshot_rows.py::test_cmgb_iguopin_snapshot_contains_only_verified_geoscience_rows
python -m pytest -q
python -m compileall -q job_hub
docker compose config
python -m job_hub.cli audit
```
