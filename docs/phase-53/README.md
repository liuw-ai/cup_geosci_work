# Phase 53：国聘动态浏览器采集器契约

## 目标

把国聘校园招聘从一次性人工快照推进到可在服务器浏览器 worker 中重复执行的采集链路，同时保持学生端专业门禁、官方证据和失败隔离。这个阶段只建设可验证的采集能力，不把未完成的浏览器运行结果冒充岗位。

## 已完成

- 新增 `job_hub/cmgb_browser_capture.py`：国聘专用捕获契约、字段校验、官方域名白名单、捕获新鲜度、分页完整性和详情失败门禁。
- 新增 `job_hub/cmgb_browser_worker.py`：独立周期 worker，支持 headless-shell CDP，失败时写入 `partial`/`access_limited` 捕获而不是空结果。
- `job_hub/sources.py` 增加 `cmgb_browser_rows` 适配器；每行保留岗位、单位、专业、学历、地点、人数、截止日期和官方详情证据。
- `docker-compose.browser.yml` 增加独立 `cmgb-browser` 服务，与 CNPC worker 分离。
- 新来源 `cmgb-iguopin-browser` 已注册但保持 `enabled: false`，直到服务器完成首轮真实完整捕获，避免和现有 33 条人工快照重复。
- 兼容详情页 `text`/`generic` 节点：采集器从渲染后的正文提取带标签字段，字段缺失即失败。

## 未声称完成

本阶段尚未在云服务器上完成一次 8 页全量浏览器运行，因此不能宣称国聘已经每日自动更新，也不能宣称国内岗位总量目标已经完成。现有学生端继续使用 Phase 52 的人工核验快照；动态来源只有在首轮捕获通过后才可切换。

## 验收入口

- 国聘列表：<https://cmgb.iguopin.com/jobCampus>
- 官方详情：<https://www.iguopin.com/job/detail>
- 服务器 worker：`cmgb-browser`（首次启用前需在 `.env` 和来源配置中完成审阅）

