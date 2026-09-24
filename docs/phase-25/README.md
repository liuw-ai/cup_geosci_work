# Phase 25：每日政府职位表刷新与运行质量摘要

本阶段把政府职位表附件纳入常驻 worker 的每日链路，重点保证事业编和公务员来源不会依赖人工忘记执行一次性命令。

## 运行行为

每次 worker 同步周期都会按以下顺序执行：

```text
加载并校验 government_artifact_manifest.json
    -> 幂等登记官方公告/附件元数据
    -> 不重置已下载、已解析或失败的处理状态
    -> 受控处理 registered 附件
    -> 生成政府附件质量摘要
    -> 保存覆盖质量快照
```

登记仍然不会下载文件或发布岗位。附件必须经过受控下载、文本/PDF/Excel 提取、岗位级专业/学历/地点/截止日期证据和管理员复核，才可进入学生端。

日报新增 `government_quality` 字段，包含登记数、当前/历史附件数、解析状态、待人工复核数、来源故障数和解释字段。公务员年度职位表未正式发布或访问失败时，摘要会显示为待核验/来源不可用，绝不生成虚假岗位。

## 配置

默认清单为 `data/government_artifact_manifest.json`。服务器可通过以下环境变量指定挂载后的清单路径：

```text
GOVERNMENT_ARTIFACT_MANIFEST_PATH=/app/data/government_artifact_manifest.json
GOVERNMENT_POSITION_REGISTRY_PATH=/app/data/government_position_registry.json
```

因此部署后不需要手动执行 `register-government-artifacts`；该命令仍保留用于管理员排查和一次性检查。日报同时读取职位表台账，显示公务员年度职位表尚未发布、来源故障或岗位行待复核等状态。

## 验收

- worker 重启后会自动补做当日同步和 20:00 日报；
- 同一附件重复同步不会重复下载或把 `extracted/failed` 重置为 `registered`；
- 来源故障、附件失败和人工复核队列会进入日志、质量摘要和邮件内容；
- 学生端只读取已通过既有发布门禁的岗位。
