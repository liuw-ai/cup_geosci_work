# Phase 10 数据与迁移说明

## SQLite

本阶段没有新增或修改 SQLite 表、列、索引或迁移脚本。现有数据库结构已经能够保存：

- 来源 ID 和来源层级；
- 官方原文 URL、官方证据 URL；
- 单位、地点、发布日期、截止日期；
- 学历和专业标签；
- `external_id`、内容指纹和来源健康状态。

首次运行 `init` 或 `sync-source` 时，`cnpc-bgp-recruitment` 会通过现有来源引导流程写入来源表；同步成功后新增一条岗位记录。重复同步使用同一来源和 `external_id`，会更新记录而不会重复插入。

本机验证结果：`discovered=1`、`open_matches=1`、`created=1`。运行库位于 `runtime/`，该目录被 Git 忽略，不随代码提交。

## 静态注册表变化

- `data/sources.json`：新增 1 个 `structured_opening_page` 来源，注册总数由 64 增至 65。
- `data/organization_registry.json`：为 `cnpc-bgp` 增加官方公开招聘频道，并绑定新来源。
- `data/national_source_matrix.json`：新增 BGP 的公开 HTML 来源评估，扫描结论为 `scan_success_with_open_matches`；目标频道类型增加 `official_announcement`。
- `job_hub/contracts.py`：增加来源类型和“扫描成功且存在匹配”状态，并对结构化来源配置执行最小契约校验。

## 回退

代码回退到本阶段前的 `phase-9-review` 即可撤销适配器和静态注册表变化。若运行库已经同步过 BGP 岗位，不需要破坏性删除；管理员可在审阅确认后使用已有的精确来源清理命令处理运行数据。生产部署应先备份 SQLite，再执行任何数据清理。
