# 2026-10-27 审计附录

日期：2026-10-27（Asia/Shanghai）  
性质：只读审计附录；本文件不代表发布、部署或生产数据库迁移。

## 1. 冻结的本地事实

本次从 `runtime/job_hub.sqlite3` 复制出临时只读审计副本，副本连续读取哈希一致：

```text
SHA-256: 4A121F9C2D64A1FDFC1EBF1508908AEB3AE62199F7753B2E2BCC8B81F8298B0B
岗位总数: 3956
生命周期: open 3547 / expired 313 / withdrawn 51 / superseded 45
持久化发布状态: out_of_scope 2651 / pending_evidence 971 / student_eligible 334
第三方线索: 12（published 9 / rejected 2 / candidate 1）
第三方批次: 0
```

当前业务日期 `2026-10-27` 下，只读阻断审计结果：

```text
当前生命周期有效: 3526
普通学生读路径: 675
当前规则重算后去重可通过: 427
全库持久化状态与重算不一致: 804
当前有效记录不一致: 763
```

普通学生读路径的代表性组成是：

```text
明确专业层（exact/broad）: 147
来源暂不可用但保留官方证据: 62
待核证据进入普通路径: 461（其中 436 为 incomplete_but_basic_match，25 为 source_unavailable）
已过期的待核记录: 5
```

系统的独立 `official_pending` 查询显示当前待核库存约 820 条，其中 `incomplete_but_basic_match` 约 511 条、`source_unavailable` 约 25 条。675 不是全部待核库存，而是普通学生路径实际混入的部分待核记录。

## 2. 统计口径冲突

覆盖率报告在同一快照中返回：

```text
open_jobs: 675
domestic_open_jobs: 607
current_job_metrics.current_valid_jobs: 607
dual_axis.coverage_axis.domestic_effective_jobs: 209
pending_evidence_jobs: 868
```

因此 `current_valid_jobs` 不能直接解释为“已确认可投岗位”。严格有效岗位为 209，其中明确专业层约 147，来源暂不可用但保留历史官方证据约 62。报告源码注释声称待核岗位“不计入当前有效岗位”，但实际公共 CTE 又允许部分待核层进入 `open_jobs`，需要统一。

建议后续固定三个指标名称：

1. `verified_current_jobs`：明确通过专业、学历、地点和生命周期门禁的岗位。
2. `source_unavailable_jobs`：保留官方证据但来源当前不可用的岗位，单独计数和提示。
3. `official_pending_jobs`：官方来源观察记录但岗位级证据未完成的岗位，只进入待核页面。

## 3. 换机后的路径风险

`.env` 使用 `APP_DATA_DIR=runtime` 和 `APP_DATABASE_PATH=runtime/job_hub.sqlite3`。这两个值是相对当前工作目录解析的：

- 从项目根目录运行时，指向项目内 `runtime`；
- 从上级目录或快捷方式运行时，会指向其他目录下的 `runtime`。

旧 `.venv/pyvenv.cfg` 还指向已失效的旧电脑 Python 和旧工作区。当前 `.venv-rebuilt` 为 Python 3.12.14，尚未与历史生产 Python 3.11 完全对齐。

此外，CLI 在大多数报表分支前统一调用可写的 `services()`，该函数会执行数据库初始化和来源引导。只有 `job-blocker-audit` 明确使用只读连接。错误目录下运行 `coverage`、`growth-metrics` 或 `discovery-funnel` 可能初始化错误位置的数据库。

## 4. 第三方与政府渠道

第三方链路已经具备候选保存、官方 URL 绑定、管理员核验和统一发布门禁，但正式库没有第三方批次记录，CLI 漏斗也没有完整传入批次数据。华图隔离试点解析 12 行、生成 11 条候选、发布 0 条，主要是专业不属于地学范围；华图附件域名还受到 robots 限制。

政府来源矩阵共 155 个目标格：`verified=19`、`candidate=38`、`unlocated=98`。事业单位招聘角色 31 个目标格全部未验证。政府台账 `as_of=2026-09-27`，包含 23 个职位批次和 9 条岗位级记录，仍需刷新和补齐地点、附件及截止状态证据。

补充复核发现，政府台账存在两种记录层级，不能混为一个数字：顶层 `records` 只有 9 条，但 23 个 `position_batches` 展开后共有 157 条岗位行。质量报告统计为：`verified_open=141`、`verified_closed=14`、`manual_review_required=2`。按 2026-10-27 的截止日期和开放政策重新计算，仍有 41 条 `open_until_filled` 行可作为潜在当前岗位；这 41 条不是已经进入学生端的岗位，也不是可以跳过来源复核的发布清单。

这 41 条仍受以下门禁阻断：台账 `as_of=2026-09-27`，年龄约 720 小时，配置上限为 48 小时；政府来源连续刷新通过数为 0；4 个来源仍需要可用性确认。另有中国气象局批次的 91 条岗位截止日期为 2026-10-26，不能在 2026-10-27 继续算当前岗位。正确结论是：政府渠道已经形成了较大的岗位级证据储备，但尚未形成新鲜、可自动发布的学生端增量。

与正式本地岗位库逐来源对照：这 41 条潜在当前岗位主要来自湖南 34、宁夏 4、甘肃 2、湖北 1；正式库中对应来源只有 7 条记录（安徽 3、湖北 3、湖南 1），且 7 条均为 `pending_evidence`，甘肃和宁夏的 6 条高价值来源记录尚未投影为正式岗位。这里存在“政府台账有岗位行，但岗位库没有对应记录”的独立断点，后续应通过受控导入和统一学生门禁处理，不能直接把台账行计入学生端。

## 5. 公网与生产边界

2026-10-27 从换机环境重新访问：

```text
cupgeojob.com A: 81.70.62.174
https://cupgeojob.com/healthz: TLS connection reset
https://cupgeojob.com/api/jobs?page=1: TLS connection reset
```

因此线上 R4 回执中的 4494/4496 检查量和 749 条学生端岗位目前只能作为部署历史汇总，不能替代服务器 SQLite 逐岗位审计。服务器备份路径仍需取得只读副本：

```text
/var/lib/job-hub/backups/job_hub-20261024T052020Z.sqlite3
```

## 6. 当前状态

- Phase 7.2：本地候选实现，尚未形成 release、推送或部署。
- 本附录：只记录事实，不改变学生端门禁和生产数据。
- 在用户明确批准前，不修改普通学生读路径、不迁移发布状态、不启用生产邮件、不轮换生产密钥。
