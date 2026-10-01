# Phase 102: Browser Worker Readiness Gate

本阶段修复动态浏览器来源的生产就绪误判。此前 `production-readiness`
只检查普通 `worker` 心跳；即使 CNPC、CNOOC 或国聘浏览器 worker 停在
`degraded`/`capturing`，系统仍可能报告内部 ready。

## 变更

- 新增 `browser_worker_health`，按已启用的浏览器来源检查对应容器心跳。
- 缺失心跳、时间格式无效、超过 4 小时未更新或状态为 `degraded` 时，内部
  readiness 失败。
- 未启用的浏览器来源不参与门禁；普通 HTML/API 来源行为不变。
- 不修改岗位数据、发布门禁或访问策略；该检查只影响运维就绪判断。

## 验收

- 本地全量回归：`449 passed, 1 skipped`。
- 服务器在线备份：SQLite 完整性通过，备份时间为 `2026-10-01T03:39:16Z`。
- 服务器主 Worker：healthy。
- CNOOC 浏览器：最近完整捕获 `343/343`，明确匹配 `43`，心跳正常。
- CMGB 浏览器：最近详情重试 `146/146`，心跳正常。
- CNPC 浏览器：最近状态为 `degraded`，原因为官方列表 HTTP 400/412；因此
  `internal_ready=false`，不会把旧 CNPC 快照标成实时成功。

## 当前阶段判断

CNOOC 已完成两次连续完整官方详情捕获，满足动态来源连续采集门槛；但 CNPC
访问受限、Sinopec 快照仍需刷新、国家管网动态捕获文件缺失，且正式域名仍未
配置，因此全院公网发布门槛尚未通过。按现有人工量表约为 **71/100**，不是
75 或 80。

