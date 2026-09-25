# Phase 33：中石油官方浏览器索引捕获与政府附件队列

本阶段把中国石油公开招聘列表的浏览器观察固化为管理员证据，并把安徽省地矿局官网的高层次人才岗位表纳入每日受控附件流水线。

## 交付内容

- 浏览器逐页读取中国石油官方招聘列表 1-13 页，记录 122 条公告索引观察。
- 单独记录勘探开发研究院、中油测井公司和东方地球物理三条重点公告详情目标。
- 详情页 `recruitInfoObj` 缺失时保留 `official_detail_api_degraded`，不将公告标题当作岗位专业证据。
- 新增管理员只读接口 `/api/admin/cnpc-browser-capture` 和 CLI `cnpc-browser-capture`。
- 安徽省地矿局官方公告附件进入 `government_artifact_manifest.json`，由 worker 自动登记并进入受控下载/解析/人工复核队列。

## 明确边界

列表公告数不是岗位数；本阶段新增可发布岗位数为 0。中国石油详情接口缺少岗位级字段时，学生端继续使用已经通过 Phase 32 门禁的快照岗位。安徽岗位表的专业、学历虽已从官方公告/岗位表确认，但地点证据和附件逐行复核未完成前不公开。

## 运维检查

```bash
python -m job_hub.cli cnpc-browser-capture
python -m job_hub.cli government-position-audit --today 2026-09-25
python -m job_hub.cli audit
```

管理员接口需要 `X-Admin-Token`；学生端不返回捕获明细、浏览器错误日志或待复核岗位。
