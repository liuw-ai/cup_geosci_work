# Phase 4 迁移说明

## SQLite

本阶段没有数据库模式迁移，没有新增表、列、索引或数据清理操作。现有岗位、来源健康、抓取运行、证据、附件和日报数据均保持不变。

省级核验信息属于低频、可审阅的配置与证据台账，因此使用版本控制中的 `data/source_validation_registry.json`，而不是把静态样例复制进 SQLite。运行时健康状态仍从已有 `sources`、`source_health` 和 `crawl_runs` 表读取。

## 新增与修改

| 路径 | 作用 | 数据边界 |
| --- | --- | --- |
| `data/source_validation_registry.json` | 9 条省级来源核验记录、样例、字段证据、备用入口、夹具路径 | 管理员/版本库 |
| `job_hub/source_validation.py` | 交叉验证台账与来源、目标矩阵，生成汇总和管理员行 | 只读 |
| `tests/fixtures/source_validation/` | 六个基于真实官方公告结构的最小离线 HTML 夹具 | 测试，不是公开岗位 |
| `tests/test_source_validation.py` | 夹具采集回归、管理员接口边界和候选停用检查 | 测试 |
| `job_hub/contracts.py` | 核验阶段、日期、相对路径和记录完整性契约 | 只读校验 |
| `job_hub/source_targets.py` | 候选 source_id 状态约束 | 只读校验 |
| `job_hub/coverage.py` | 公开只返回核验聚合指标 | 学生端聚合 |
| `job_hub/app.py` / `job_hub/cli.py` | 管理员 API 和 CLI | 令牌保护/本地命令 |

## 回退

本阶段为加法式配置和只读代码变更。回退到 `v0.7.0` 不需要数据库降级；删除或回退 Phase 4 文件即可恢复之前的运行行为。审阅前不合并 `master`，不创建 `v0.8.0`。

## 运行注意

- `adapter_fixture_verified` 不会改变 `enabled` 字段。
- 山东 `shandong-hrss-exam` 仍是 `candidate` 目标和 disabled 来源。
- 本地命令行 TLS 失败只能记录为当前环境无法验证，不能写成官方来源不存在。
- 夹具不包含完整公告页面，也不触发外部网络请求；真实附件不会下载进 Git。
