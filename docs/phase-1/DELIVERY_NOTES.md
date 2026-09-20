# Phase 1 交付说明

## 交付身份

| 项目 | 值 |
| --- | --- |
| 工作分支 | `phase/1-data-contract` |
| 起始基线 | `9c75885`，`v0.4.0` |
| 阶段目标 | 建立来源、单位、岗位、证据、附件与私有线索的数据契约。 |
| 审阅标签 | `phase-1-review` |
| 正式版本 | 审阅通过并合并后才创建 `v0.5.0`。 |

## 变更清单

- 新增 `job_hub/contracts.py`，集中维护数据合同和状态转换矩阵。
- 扩展 SQLite schema 与可重复迁移，增加附件、证据和线索状态历史。
- 岗位保存、人工导入和候选线索发布都自动保留官方原文证据。
- 发布前审计新增“已核验官方页面/记录证据”质量门槛。
- 新增三个私有管理员 API，不修改学生端视觉页面或公开 JSON。
- 新增合同、证据、迁移、私有接口和状态机测试；更新旧线索测试以遵循官网定位步骤。

## 数据库影响

新表和回填行为见 [迁移说明](MIGRATION_NOTES.md)。本阶段不会下载附件或创建附件正文，`content_sha256`、`storage_path`、`parser_version` 只为后续受控解析预留。

## 验证命令

```powershell
python -m compileall -q job_hub tests
python -m pytest -q
git diff --check
```

完整回归、离线注册表合同检查和 Phase 1 临时库迁移检查均已在提交前执行：

```text
python -m pytest -q
88 passed in 1.51s

source registry: 25
provincial source registry: 38
employer registry: 36
registry contracts: OK
```

## 审阅重点

1. 公开岗位是否仍只允许官方原文进入学生端。
2. `candidate -> official_url_found -> official_content_verified -> published` 是否符合人工核验习惯。
3. 附件台账字段是否足以让 Phase 2 解析 PDF/Excel 时保存哈希、来源、状态和行级证据。
4. 管理员接口是否足够，但没有把线索和证据暴露给学生。
5. 迁移和回退说明是否适合未来服务器部署。

## 下一阶段前置条件

只有 Phase 1 被审阅通过后，Phase 2 才开始：为已核验、公开允许访问的官方公告附件建立受控下载、PDF/Excel 文本表格提取、扫描件 OCR 兜底和逐岗位证据关联。任何一个具体网站的附件行为、robots、下载权限和字段结构都必须单独验证，不能由本数据契约自动推断。
