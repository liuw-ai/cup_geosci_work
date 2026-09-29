# Phase 77 迁移说明

## 配置迁移

- `slb-career.detail_time_budget_seconds`：`50` -> `120`。
- 数据库结构无变化，不需要迁移脚本。
- 不删除或覆盖旧岗位；Worker 继续依据截止日期和官方来源状态清退过期记录。

## 服务器切换

1. 保留旧工作区和数据库备份。
2. 在新工作区构建 Web/Worker；浏览器镜像若因依赖下载失败，保留已运行的浏览器容器并记录为部署风险，不把它伪装成采集成功。
3. 启动后依次执行 `audit`、`worker-health`、`sinopec-capture --require-complete --require-scan-complete` 和 `production-readiness`。
4. 只有所有结果与域名 HTTPS 核验一致，才允许把新工作区作为正式服务版本。
