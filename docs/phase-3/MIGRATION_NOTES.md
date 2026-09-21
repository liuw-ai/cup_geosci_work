# Phase 3 迁移说明

## 数据库迁移

本阶段没有 SQLite 模式迁移，也没有修改已有岗位、日报、证据、附件或私有线索数据。

组织层级和正式入口属于低频、可审阅的配置数据，因此采用版本化 JSON 文件 `data/organization_registry.json`，而不是新增数据库表。这样可在不影响生产岗位库的前提下审阅、回退和逐步完善单位矩阵。

## 新增文件与运行行为

| 路径 | 作用 | 写入时机 |
| --- | --- | --- |
| `data/organization_registry.json` | 组织、父级、产业角色、官方频道、备用入口与 `source_id` 绑定 | 版本控制内人工审阅后更新 |
| `job_hub/organizations.py` | 加载、验证、查询、聚合和管理员展示行 | 只读 |
| `tests/test_organizations.py` | 注册表、角色分离、来源绑定、层级循环和主频道回归测试 | 测试期临时内存数据 |

启动 Flask、运行 `coverage` 或执行 `organization-matrix` 时会读取该 JSON 文件。读取失败或违反契约会明确报错，而不是悄悄忽略一个失效入口。

## 回退

Phase 3 只引入加法文件和读路径；回退到 `v0.6.0` 不需要数据库降级。审阅通过后会创建稳定标签 `v0.7.0`。在此之前，审阅分支和 `phase-3-review` 标签是唯一建议的检查入口。

## 运维影响

- 不需要新增环境变量。
- 不需要 Docker Compose 改动。
- 不触发网络采集。
- 不增加 student-facing API 字段中的频道明细；公开覆盖接口只增加聚合组织指标。
- 管理员接口继续使用既有 `ADMIN_TOKEN` / `X-Admin-Token` 鉴权机制。
