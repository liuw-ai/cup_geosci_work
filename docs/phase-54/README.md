# Phase 54: 国聘动态详情证据链

本阶段的目标是让国聘校园招聘动态页面能够通过服务器浏览器逐页打开官方详情页，并把详情页中的完整字段送入既有发布门禁。

## 范围

- 只读取公开页面，不调用隐藏写接口，不绕过 robots 或登录限制。
- 列表卡片不是岗位证据；岗位必须来自 `www.iguopin.com/job/detail?id=...` 官方详情页。
- 标题、单位、地点、招聘人数、最低学历和报名截止从详情页结构化区域读取。
- 当摘要只写“详见职位描述”时，必须从职位介绍正文提取明确专业；无法提取则失败隔离。
- 分页未完成、任一详情失败或字段缺失时，捕获状态为 `partial`/`parse_failed`，不得发布成“无岗位”。

## 本阶段不声称

本阶段只验证国聘一套动态来源，不代表三桶油、31 省事业编、公务员和全国国内岗位扩容已经完成。后续仍需逐来源接入并按同一证据标准验收。

## 验收入口

- 解析器：`job_hub/cmgb_browser_capture.py`
- 浏览器 worker：`job_hub/cmgb_browser_worker.py`
- 真实 DOM 夹具：`tests/fixtures/domestic/cmgb_iguopin_detail.html`
- 专项测试：`tests/test_cmgb_browser_capture.py`
- 服务器捕获：`/opt/cup_geosci_phase54/captures/cmgb-iguopin-browser.json`
