# Phase 16：官方附件证据统一与历史漏发修复

本阶段没有放宽学生端门禁，也没有把“行业相关”当作专业匹配。修复对象是附件岗位表流水线：旧版解析器把岗位、专业和学历放在 `field_evidence.fields` 内，却没有写入统一的岗位级证据范围和岗位标题绑定，导致真实、已人工核验的地学岗位在 Phase 15 门禁下被错误降级为 `pending_evidence`。

## 本阶段变更

- 新增统一的 `build_attachment_field_evidence` 转换函数。
- 每个官方附件行现在明确写入：`evidence_scope=official_attachment_row`、`岗位`、`专业范围`、`学历要求`、`table_row` 和附件 URL。
- 新候选、管理员发布接口和历史候选迁移均使用同一证据格式，避免不同入口产生不同门禁结果。
- 新增 `repair-attachment-evidence` 命令：只修复附件证据结构，保留候选审核状态；不会自动发布 `needs_review` 候选。
- 为历史已人工核验的附件岗位增加 `evidence_repaired` 事件，并重新运行统一发布门禁。

## 真实数据结果

- 安徽省地质矿产勘查局官方附件已存在 22 条解析候选，其中 2 条此前已完成官方内容核验。
- 迁移后这 2 条岗位恢复为 `student_eligible`：
  - 专业技术（地质学、地质资源与地质工程、地球物理学）
  - 专业技术（地质学）
- 官方公告：[安徽工业经济职业技术学院2026年高层次人才招聘公告](https://dkj.ah.gov.cn/xwzx/tzgg/40788529.html)
- 官方岗位表：[岗位汇总表.xls](https://dkj.ah.gov.cn/group3/M00/14/5E/wKg86mnx3JSAe_TTAAA0PEnJC-Q385.xls)

## 运行结果

- 原始记录：226 条
- 学生端公开在招：32 条
- 本阶段新增公开：2 条，来自历史已核验官方附件岗位
- 100 人模拟：仍保持每人有明确匹配机会
- 来源集中度：仍以国家管网为主，扩源目标尚未完成

## 运维命令

```powershell
python -m job_hub.cli repair-attachment-evidence
python -m job_hub.cli reindex-jobs
python -m job_hub.cli publish --refresh
python -m job_hub.cli audit
```

迁移命令可重复执行；第二次运行不会重复写入相同证据。任何新附件仍必须经过官方原文核验和管理员审核后才能发布。

## 验收

- `pytest -q`：见交付记录
- `python -m compileall -q job_hub tests`：通过
- `python -m job_hub.cli audit`：`ok: true`
- 质量快照：[coverage-after-attachment-evidence-repair.json](coverage-after-attachment-evidence-repair.json)

