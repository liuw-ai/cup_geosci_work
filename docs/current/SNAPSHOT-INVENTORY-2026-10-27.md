# 数据库快照清单与线上事实边界

日期：2026-10-27（Asia/Shanghai）  
性质：只读清点；本记录没有部署、没有写入岗位数据、没有修改生产配置。

## 结论

工作区没有 `phase7-20261024-r4` 的线上 SQLite 副本。已有本地文件最多到 2026-10-23，且均有明确的本地验证、回放或历史备份用途，不能替代线上 R4。

线上 R4 回执列出的服务器备份路径为 `/var/lib/job-hub/backups/job_hub-20261024T052020Z.sqlite3`，该文件不在当前工作区；必须从服务器取得只读副本或逐岗位脱敏导出后，才能对线上 749 条学生端岗位之外的记录逐条归因。

## 可用本地快照

| 文件 | 文件时间 | SHA-256 | 审计结果 | 用途边界 |
|---|---|---|---|---|
| `runtime/job_hub.sqlite3` | 2026-10-27 22:17 | `8A07BDD54E407C674F535525868B223608A395DA61F1C2CD763B73D274116D89` | 3956 条；当前读路径 675；当前规则重算约 427 | 当前正式本地库，不是线上 R4 |
| `backups/job_hub-before-phase5-baseline-restore-20261022.sqlite3` | 2026-10-22 12:42 | `C0402375B9D8FC0A930CE06FCC564CFF35BE806FB18CD3A21EE6BCFD933666E0` | 3956 条；当前读路径 833；当前规则重算约 445 | Phase 5 基线恢复前历史快照 |
| `runtime/backups/job_hub-20261021T063531Z.sqlite3` | 2026-10-21 14:35 | `A95556D2CCA1F737C75917F67E403263D73BD2ADC954AEDA69C7F8E9B4683C03` | 3956 条；当前读路径 675；当前规则重算约 427 | 2026-10-21 本地备份 |
| `runtime/phase6/review-2026-10-23-r3/job_hub.sqlite3` | 2026-10-23 02:51 | `C951368490922C6B0568C81A1B4CA89D50DE086AE2ACBA73B7B9CCAB4CAFF74C` | 3956 条；当前读路径 675；当前规则重算约 427 | Phase 6 隔离审查库，不是生产库 |
| `runtime/phase7/ui-smoke/job_hub.sqlite3` | 2026-10-23 13:54 | `3EC952CA427EAE10708116D6DE13B9858771033CC0521784324CA3CFBA0F012C` | 0 条岗位 | UI 烟测空库，不可用于数据审计 |

所有上述审计均使用 `job-blocker-audit --database ...` 的只读连接，业务日期固定为 2026-10-27；没有把任何快照当作线上 R4。

## 为什么历史快照不能合并解释

两个均为 3956 条岗位的快照，学生端读路径分别返回 833 和 675，按当前规则去重重算分别约为 445 和 427。这说明岗位总数不能推出学生端数量，历史状态、规则版本、证据重建和去重口径都影响结果。

前一份审计记录曾为 `runtime/job_hub.sqlite3` 记录 SHA-256 `2b7d0752...dbcd43b`；本次重新清点得到新的文件哈希，但岗位总数和持久化状态计数仍为 3956、`out_of_scope=2651`、`pending_evidence=971`、`student_eligible=334`。SQLite 使用 WAL/SHM 时，checkpoint 和页级元数据可能改变文件哈希；后续审计必须同时保存文件哈希和岗位逻辑摘要，不能继续引用未重新锁定的旧哈希。

补充验证：将当前正式库复制到临时文件后运行同一只读审计命令，前后 SHA-256、文件长度和写入时间均完全一致，且没有生成临时 `-wal/-shm` 文件。因此本次审计没有造成正式库哈希变化；`2b7d0752...` 与当前哈希的差异发生在更早的数据库生命周期中，仍需以重新锁定的快照为准。

## 线上审计放行条件

拿到 R4 副本后，必须在不写入副本的前提下记录：

1. 文件 SHA-256、文件来源和服务器备份时间；
2. release ID、业务日期和规则版本；
3. 总岗位、当前有效、学生公开、待核验、明确排除和私有候选六类数字；
4. 学生端去重规则及逐岗位阻断原因；
5. 逻辑岗位摘要，用于区分 SQLite 文件层变化和真实岗位内容变化。

在此条件满足前，线上回执中的 4494/4496 条检查量和 749 条学生端公开数只能作为汇总证据，不能生成线上逐岗位阻断比例。

## 公网只读验证

2026-10-27 从当前换机环境对以下公开地址各执行一次 `curl.exe -L --max-time 20`：

- `https://cupgeojob.com/healthz`
- `https://cupgeojob.com/api/jobs?page=1`
- `https://cupgeojob.com/api/coverage`

三次均在 TLS/HTTP 响应前失败，错误为 `curl: (35) Recv failure: Connection was reset`。因此当前不能通过公网 API 获取线上岗位样本，也不能把公网不可达解释为“线上没有岗位”；域名/TLS/安全组仍是生产放行阻断项。

## 当前本地学生读路径的额外阻断发现

对当前正式本地库按 2026-10-27 执行只读 SQL 后，学生读路径的 675 条组成如下：

| `publication_status` | `publication_layer` | `status` | 数量 | 判断 |
|---|---|---|---:|---|
| `pending_evidence` | `incomplete_but_basic_match` | `open` | 436 | 待补证据，不应与明确公开岗位混计 |
| `student_eligible` | `exact_match` | `open` | 140 | 明确专业匹配 |
| `student_eligible` | `source_unavailable` | `open` | 62 | 有历史官方证据，但来源当前不可用，需单独标注 |
| `pending_evidence` | `source_unavailable` | `open` | 25 | 待核证据，不应与公开岗位混计 |
| `student_eligible` | `broad_geoscience_match` | `open` | 7 | 宽泛地学匹配，需提示学生复核资格 |
| `pending_evidence` | `incomplete_but_basic_match` | `expired` | 5 | 已过生命周期，不应进入当前集合 |

因此本地正式库中，严格按学生公开状态和允许公开层计数为 `140 + 62 + 7 = 209`；读路径的 675 与增长指标的 209 之差为 466，主要正是待核证据和生命周期状态造成的，不是新增岗位。

代码证据：`_student_visible_jobs_cte()` 的条件使用 `publication_status ... OR publication_layer IN (..., 'incomplete_but_basic_match', ...)`，而 `/api/jobs` 和普通学生页面通过 `list_jobs(..., as_of_date=business_date)` 使用该 CTE；`official_pending` 虽有独立入口，但没有从普通路径中排除这些待核层。该问题尚未修改，也没有据此调整线上数据。

发布前必须补充：

1. 明确 `current` 学生路径只允许 `student_eligible` 及经过明确批准的公开层；
2. 将 `pending_evidence` 仅放在管理员或显式 `official_pending` 视图；
3. 为 `/api/jobs`、`/jobs`、`find_public_job` 和增长指标增加同一口径的集成测试；
4. 在 R4 线上副本复现后再决定是否迁移持久化状态。
