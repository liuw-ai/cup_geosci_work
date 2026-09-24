# Phase 24：省级事业编附件队列与公务员职位代码契约

本阶段针对山东、河南、天津三个官方事业单位来源，完成已核验公告附件的服务器受控下载队列，并把公务员年度职位表需要的职位代码字段纳入统一契约。

## 完成内容

- 新增 `data/government_artifact_manifest.json`：登记山东、河南、天津的官方公告和 PDF/Excel 职位表附件。
- 新增 `job_hub/government_artifacts.py`：校验附件 URL、公告 URL、省份、报名截止日期、来源状态，并只登记私有附件元数据。
- 新增 CLI：

```text
python -m job_hub.cli register-government-artifacts
```

- `job_hub/government_positions.py` 的逐岗位契约新增 `position_code`，用于事业编岗位代码和未来国家公务员职位代码。
- 官方附件候选现在自动提取“职位代码/岗位代码/职位编号/岗位编号”等列，并写入岗位级证据。

## 服务器执行顺序

```text
register-government-artifacts
        -> process-artifact <artifact_id>
        -> list-artifact-candidates --status needs_review
        -> 管理员核验专业、学历、地点、截止日期和职位代码
        -> 通过既有发布门禁后进入学生端
```

本阶段登记的三个 2026 公告报名期均已结束，因此不会被标为当前在招。它们用于历史岗位追踪、附件解析回归和下一次公告结构复用。服务器发现更新公告后，应新增 manifest 行，不覆盖旧附件。

国家公务员局当前年度职位表仍未确认公开；发布后应以官方 PDF/Excel 为唯一输入，要求职位代码、招录机关、专业代码/名称、学历、地点、人数、报名截止日和原始附件证据齐全后再导入。
