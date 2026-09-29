# Phase 82 迁移说明

- 无数据库结构迁移。
- 浏览器 worker 新增 `CMGB_BROWSER_CAPTURE_TIMEOUT_SECONDS`，默认 `1800` 秒。
- 更新浏览器 worker 后，若旧进程仍停留在 `capturing`，应重启 `cmgb-browser` 容器；旧成功捕获文件和数据库岗位不会被删除。
