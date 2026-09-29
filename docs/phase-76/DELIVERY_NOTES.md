# Phase 76 交付说明

- 分支：`phase/76-production-audit-repair`
- 目标：修复生产审计阻塞并恢复中石化官方 132/35 单位快照。
- 数据库迁移：无。
- 真实官方证据：中石化官方 SPA 只读接口及单位详情路由；CGS 官方详情域名白名单。
- 新快照：`data/verified/sinopec-geoscience-20260929.json`，363 条岗位行。
- 浏览器运行时：仅重建 headless-shell 容器，未停止 Web、Worker 或删除数据库。
- 当前边界：尚未完成中石化独立每日自动捕获 Worker，也未因此宣称全院正式版完成。
- 回退点：`phase-75-review`。
