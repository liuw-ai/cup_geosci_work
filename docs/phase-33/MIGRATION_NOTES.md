# Phase 33 迁移说明

本阶段没有新增 SQLite 表，也没有改变公开岗位结构。新增的是版本化浏览器证据文件、只读审计模块和管理员接口。

部署步骤：

1. 同步代码、`data/verified/cnpc-browser-index-20260925.json` 和 `data/government_artifact_manifest.json`。
2. 重建 web/worker 容器，worker 启动时会幂等登记安徽官方 XLS 附件。
3. 在服务器执行 `docker compose exec web python -m job_hub.cli audit`。
4. 用管理员令牌检查 `/api/admin/cnpc-browser-capture` 和附件候选队列。

附件下载失败、来源 WAF/TLS 或地点证据缺失时，记录为来源故障/待复核，不改变学生端公开岗位。
