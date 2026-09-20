# Phase 2 数据迁移与运行目录说明

## SQLite 加法迁移

`Database.initialize()` 继续使用 `CREATE TABLE IF NOT EXISTS`，新增两张表和索引：

| 表 | 内容 | 公开性 |
| --- | --- | --- |
| `source_artifact_rows` | 每个官方附件的 sheet/页码、行号、单元格、行文本和提取置信度 | 管理员私有 |
| `artifact_job_candidates` | 从原始行派生的岗位候选、字段证据、审核状态和可能绑定的公开岗位 | 管理员私有 |

没有删除或改写既有 `jobs`、`daily_reports`、`crawl_runs`、`source_artifacts`、`job_evidence` 或 `candidate_leads` 数据。既有附件台账仍保持 `registered`，只有显式运行处理命令才会下载文件。

迁移前建议：

```bash
docker compose stop worker
docker compose exec web python -c "from job_hub.config import Settings; print(Settings.from_env().database_path)"
docker compose cp web:/var/lib/job-hub/job_hub.sqlite3 ./backup/job_hub-pre-phase-2.sqlite3
docker compose up -d worker
```

初始化后可检查：

```bash
docker compose run --rm web python -m job_hub.cli init
docker compose run --rm web python -m job_hub.cli audit
```

## 附件存储

文件不进入 Git，也不由 Flask 静态路由提供。下载文件写入：

```text
APP_DATA_DIR/
  official-attachments/
    sha256/<前两位>/<完整 SHA-256>.<扩展名>
```

`ATTACHMENT_STORAGE_DIR` 使用相对路径时，会相对于 `APP_DATA_DIR` 解析；因此示例配置使用 `official-attachments`，不会在本地形成重复的 `runtime/runtime` 路径。无论使用相对还是绝对路径，最终目录都必须位于 `APP_DATA_DIR` 内。

PDF 文本层或 OCR 文本（如果存在）以同一文件名追加 `.txt` 保存。数据库只记录相对 `storage_path` 和 `text_storage_path`；所有路径在写入前都会拒绝绝对路径和目录穿越。

下载采用临时 `.part` 文件，完成哈希后再原子改名；同哈希文件不会重复保存。大小、响应类型、重定向域名或 robots 检查失败时，不会留下可处理的半文件。

## 依赖和镜像

`requirements.txt` 增加 `openpyxl` 和 `pypdf`。Docker 镜像增加 `poppler-utils`、`tesseract-ocr` 和简体中文语言包，但 OCR 仍须通过环境变量显式开启。Windows 本地没有 Tesseract 时，扫描 PDF 会保留“需要 OCR/不可自动提取”状态，不会伪造文字。

## 回退

代码可精确回退到 `v0.5.0`。如果已经下载附件或创建私有候选，回退前应保留整个数据库和 `official-attachments` 目录；旧版本不会读取新表和附件文件，但删除新表会丢失审核证据。不要只复制 SQLite 的 `-wal` 文件来做备份。
