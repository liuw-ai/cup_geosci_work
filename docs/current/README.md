# 当前项目目录与 Docker 文件规范

本文件定义新项目应采用的目录边界和 Docker 文件保存方式。历史产物已在本地移动到 `history/`，未删除、不覆盖，也不作为新生产输入。

## 当前事实来源

当前岗位和代码事实以 [phase4-current-state-audit-2026-10-18.json](phase4-current-state-audit-2026-10-18.json) 及最新 [Phase 4 Manifest](phase4-release-manifest.json) 为准。`CODEBASE-2026-10-17.md`、Phase 3 文档和更早的快照属于历史证据；其中的数量、哈希和测试结果不能覆盖当前审计。历史文件不删除、不作为生产输入，任何迁移或清理都必须保留原始哈希。

当前本地事实的边界：正式数据库只读审计为 3948 条岗位、2 条候选线索，其中 1 条已通过官方企业公告回溯并形成正式学生岗位，另 1 条 C 级垂直渠道线索仍待官方 URL。服务器未部署，自动化保持暂停。华图试点的 11 条候选仍仅存在隔离库；第三方发现链接不作为学生申请链接。

## 1. 目标目录

```text
project/
├─ app/                 当前正式应用代码
├─ data/                可版本化的来源、组织和匹配配置
├─ tests/               单元、集成和契约测试
├─ deploy/              唯一生产部署入口
│  ├─ compose.yaml      唯一正式 Compose 文件
│  ├─ docker/           Dockerfile 和镜像构建说明
│  ├─ caddy/            反向代理配置（如启用）
│  └─ env/              非敏感环境变量模板
├─ docs/
│  └─ current/          当前有效方案、清单和验收记录
├─ runtime/             本地/服务器运行产物，不进入 Git 和镜像
├─ backups/             受控数据库备份和备份清单
└─ history/             旧代码、旧 Compose、旧快照和旧临时文件
```

`scripts/` 可以作为顶层维护工具目录保留；它只保存可重复执行的维护脚本，不保存扫描结果。`examples/` 只保存示例输入和示例配置，不是生产数据。

## 2. Docker 文件放置

### 2.1 生产 Compose

生产只认一个文件：

```text
deploy/compose.yaml
```

不再用 `phase-*`、`overlay`、`location-fix`、`fast` 等文件叠加发布。不同服务数量或浏览器 worker 应通过同一个 Compose 文件中的服务定义、profiles 和环境变量表达。任何修改都必须绑定 Git SHA、Compose SHA、镜像 digest、配置 SHA 和数据库备份 SHA，写入 release Manifest 后才允许发布。

### 2.2 Dockerfile

Dockerfile 按镜像职责放在：

```text
deploy/docker/Dockerfile.app       # Web 和 Worker 共用的应用镜像（若代码相同）
deploy/docker/Dockerfile.browser   # 浏览器 Worker 镜像
```

如果应用和 Worker 的依赖或启动方式确实不同，再增加独立 Dockerfile；不能以阶段编号复制 Dockerfile。外部的 Chromium/headless-shell 镜像只在 Compose 中引用，并在发布 Manifest 中固定 digest，不使用生产 `latest`。

构建上下文保持为项目根目录，以便同时读取 `app/`、`data/` 和依赖文件；根目录 `.dockerignore` 负责排除：

- `.git/`、`.venv/`、`__pycache__/`、`.pytest_cache/`；
- `runtime/`、`backups/`、`history/`、测试输出和日志；
- SQLite 数据库、捕获文件、附件和任何密钥。

`.dockerignore` 必须与构建上下文放在同一层。不能把数据库、浏览器截图、岗位快照或服务器备份复制进镜像。

### 2.3 环境文件和密钥

```text
deploy/env/.env.example       # 可提交的非敏感模板
.env                          # 本地开发文件，必须被 Git 忽略
.release.env                  # 生成的发布身份文件，不能放密钥
```

真实密钥、管理员令牌、SMTP 密码和服务器凭据不进入 Git、Dockerfile、Compose 或镜像层。生产通过受控环境注入或密钥管理系统提供；`.release.env` 只保存版本、digest 和构建时间等非敏感身份信息。

### 2.4 运行数据和卷

数据库、SQLite WAL、浏览器捕获、HTTP 缓存、附件和日志全部写入运行卷：

```text
runtime/
```

生产 Compose 使用命名卷或明确的服务器数据目录挂载到 `/var/lib/job-hub`。镜像必须保持可替换，不能把运行数据写入容器可写层。`backups/` 只保存受控、可校验的数据库备份，不与 `runtime/` 或 `docs/current/` 混放。

## 3. 各目录职责

| 目录 | 允许内容 | 不允许内容 |
|---|---|---|
| `app/` | 当前正式 Python 应用、模板、静态资源 | 数据库、日志、截图、旧阶段副本 |
| `data/` | 来源登记、组织目录、专业规则、版本化清单 | 运行时捕获、下载附件、私密令牌 |
| `tests/` | 测试代码、固定夹具、最小样例 | 正式数据库、真实生产凭据 |
| `deploy/` | 唯一生产 Compose、Dockerfile、代理模板、部署说明 | 阶段 overlay、临时修复 Compose |
| `docs/current/` | 当前政策、数据契约、清点、测试和发布审计 | SQLite 备份、浏览器截图、运行缓存 |
| `history/docs/` | 已冻结的历史 Phase 文档和报告 | 当前生产规则 |
| `runtime/` | 当前运行数据库、WAL、捕获、附件、缓存、日志 | Git 版本化代码和生产规范文档 |
| `backups/` | 数据库备份、SHA-256 清单、恢复记录 | 工作数据库、无来源临时文件 |
| `history/` | 已冻结的旧代码、旧 Compose、旧镜像说明、旧快照、阶段产物和缓存 | 当前生产输入 |
| `scripts/` | 可重复执行的检查、备份、发布辅助脚本 | 执行结果和大文件产物 |
| `examples/` | 脱敏示例数据和配置 | 真实岗位数据和密钥 |

## 4. 当前目录的偏差

截至 `RESET-2026-10-17` 清点，当前仍存在以下偏差：

- 正式代码位于 `job_hub/`，尚未进入 `app/`；
- 根目录有多个 Compose 和 Dockerfile；
- `deploy/` 的旧阶段和 overlay 文件已移动到 `history/deploy/`；
- 历史 `docs/phase-*` 已移动到 `history/docs/phase-*`；
- 历史 runtime 阶段、浏览器、探针和缓存产物已移动到 `history/runtime/`；
- 根目录 `official-attachments/`、`runtime-probe/` 和 `archive/` 已移动到 `history/`；
- `.worktrees/` 及仓库外活动 worktree 保留原位，以免破坏 Git worktree 元数据；
- `docs/current/` 曾出现 SQLite 备份文件，后续应移出到受控 `backups/`，但本轮不执行移动；
- 正式生产 Compose 入口已经收敛为 `deploy/compose.yaml`；根目录 Compose/Dockerfile 仅作为本地开发兼容入口保留，不得用于生产发布。

这些仍是结构治理事项，不代表已经形成可发布版本。历史移动记录及逐文件哈希见 `HISTORY-MIGRATION-2026-10-17.json`。

## 5. Docker 运行原则

1. 一个生产 Compose 文件，一个发布 Manifest，一个明确的镜像 digest 集合。
2. Web、Worker 和浏览器 Worker 的代码/策略版本必须可审计地绑定。
3. `latest` 只允许用于本地开发，不能用于生产身份或恢复依据。
4. 生产数据库和捕获证据永远来自挂载卷或备份，不来自镜像层。
5. 本地测试使用临时数据库或隔离副本，不能直接写正式 `runtime/job_hub.sqlite3`。
6. Compose、Dockerfile、环境模板和 `.dockerignore` 的每次变更都必须经过本地测试和用户审阅，未批准不得部署服务器。

## 6. 规范依据

- [Docker Build 最佳实践](https://docs.docker.com/build/building/best-practices/)：多阶段构建、缩小镜像、固定依赖和 digest、合理使用构建上下文。
- [Compose 文件参考](https://docs.docker.com/reference/compose-file/)：服务、卷、环境变量、profiles 和 secrets 的结构化定义。
- [Docker 构建上下文与 `.dockerignore`](https://docs.docker.com/build/concepts/context/)：构建上下文边界和排除运行数据的方法。

本 README 只建立保存规范；它不批准任何生产部署，也不改变当前服务器状态。

## 7. 逐文件归档清单

本轮已生成逐文件清单（当前工作区，不含 `history/`）：

- [archive-manifest.json](archive-manifest.json)：当前工作区文件的路径、大小、修改时间、SHA-256、当前分类、建议目标和建议动作；
- [generate_archive_manifest.ps1](../../scripts/generate_archive_manifest.ps1)：可重复生成清单的脚本。
- [HISTORY-MIGRATION-2026-10-17.json](HISTORY-MIGRATION-2026-10-17.json)：已移动 5,314 个文件、约 2.25 GB 的逐文件迁移和 SHA-256 校验记录；
- [archive_history.ps1](../../scripts/archive_history.ps1)：可重复校验或恢复迁移日志的归档脚本。

扫描范围是项目根目录下的当前常规文件，明确排除 `.git/`、`.venv/` 和冻结的 `history/`。清单中的建议只是待审阅意见：

- `keep_current`、`keep_runtime`、`keep_archive`、`keep_backup`：建议暂时保留原位；
- `archive_after_approval`：建议经确认后归档；
- `move_after_approval`：建议经确认后移动到规范目录；
- `clean_after_approval`：可重建缓存，仍须确认后清理；
- `review_before_action`：不能安全自动判断，必须人工逐项审阅。

当前清单生成不会移动、删除或改写文件；历史迁移已经完成且独立记录，后续归档动作仍必须先更新清单并保留原始 SHA-256。
