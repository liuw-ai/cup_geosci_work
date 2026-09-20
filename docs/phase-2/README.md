# Phase 2：官方附件职位表流水线

本阶段把已经核验的官方招聘公告附件纳入一个可审计、可重复运行的私有处理流程。服务对象仍然是中国石油大学（北京）地球科学学院：资源勘查工程本科生，以及地质学、地质工程、地质资源与地质工程硕士生和博士生。

本阶段的目标不是用解析行数充岗位数量，而是确保每一条由 PDF、Excel、CSV 或 DOCX 派生的候选岗位都能回到：

```text
已登记官方来源
    -> 官方公告页
    -> 附件链接发现（仅登记，不下载）
    -> robots/域名/大小/类型检查
    -> 受控下载 + SHA-256 + 私有存储
    -> 文本/表格解析（扫描件 OCR 仅显式启用）
    -> 原始行台账 + 字段证据
    -> 私有岗位候选（needs_review）
    -> 管理员逐条核验
    -> 官方页面 + 附件证据同时写入后才可公开
```

## 本阶段完成

- `job_hub/attachments.py` 提供单附件发现、下载、哈希、解析和候选队列能力。
- 支持 PDF 文本层、XLSX/XLSM、CSV；DOCX 在安装 `python-docx` 时解析，否则明确标记为跳过。
- 扫描 PDF 只有在 `ATTACHMENT_OCR_ENABLED=true` 时才调用 `pdftoppm` 和 `tesseract`；OCR 行标记为低置信度，不能直接发布。
- 下载文件只允许来自来源注册表的官方域名或显式 `attachment_allowed_hosts`，并逐个检查 `robots.txt`、HTTP 状态、重定向域名、文件类型和大小上限。
- 文件存放在 `APP_DATA_DIR/official-attachments` 下的哈希路径，数据库只保存相对路径、哈希、解析器版本和处理元数据；学生端没有附件目录路由。
- 新增 `source_artifact_rows` 原始行台账和 `artifact_job_candidates` 私有岗位候选表，重复处理按附件/表单/行号幂等更新。
- 候选状态必须经过 `needs_review -> official_content_verified`，并由管理员显式发布；低置信度 OCR 候选不能通过附件发布接口。
- 每个发布候选自动保留官方公告页证据，并额外写入同一附件的 `attachment` 证据和行定位信息。
- 候选发布、公开岗位、附件证据和候选状态在同一个 SQLite 事务中提交；任一校验或写入失败都会整体回滚。
- 提供管理员 API 和 CLI，但不把附件处理放进学生页面请求或每日公开同步循环。

## 公开边界

公众号、中公、华图、行业媒体和招聘聚合站仍然只是私有发现线索。附件处理器只接受已经登记、允许公开访问的单位官网、政府官网、高校/科研院所官网或正式招聘系统公告页。登录、验证码、付费下载、访问控制、robots 禁止和需要绕过的 TLS/指纹校验都会被跳过并记录原因。

解析结果只是候选，不是录用资格判断。管理员必须核对公告正文中的岗位、单位、专业、学历、地点和截止日期；学生端只看到发布后的标准化岗位和官方原文链接。

## 操作入口

先将公告页登记到已核验来源，再发现附件链接：

```powershell
python -m job_hub.cli discover-artifacts <source_id> <official_announcement_url>
```

查看管理员附件台账：

```powershell
python -m job_hub.cli init
```

受控下载并解析单个附件（`artifact_id` 来自管理员 API 或数据库台账）：

```powershell
python -m job_hub.cli process-artifact <artifact_id>
python -m job_hub.cli process-artifact <artifact_id> --download-only
python -m job_hub.cli process-artifact <artifact_id> --force-download
python -m job_hub.cli list-artifact-rows <artifact_id>
python -m job_hub.cli list-artifact-candidates --artifact-id <artifact_id>
```

管理员 HTTP 接口均要求 `X-Admin-Token`：

| 接口 | 作用 |
| --- | --- |
| `POST /api/admin/artifacts/discover` | 从一个官方公告页登记 PDF/Excel/CSV/DOCX 链接 |
| `POST /api/admin/artifacts/<id>/process` | 下载并解析一个附件 |
| `GET /api/admin/artifacts/<id>/rows` | 查看原始行和提取置信度 |
| `GET /api/admin/artifact-candidates` | 查看私有岗位候选 |
| `PATCH /api/admin/artifact-candidates/<id>` | 写入人工核验状态和说明 |
| `POST /api/admin/artifact-candidates/<id>/publish` | 将已核验候选转为公开岗位并绑定附件证据 |

## 配置与限制

`.env.example` 中的附件配置默认采取保守值：单文件 25 MB、公告页发现响应 2 MB、最多 2,000 行、提取文本最多 250,000 字符，OCR 默认关闭。相对的 `ATTACHMENT_STORAGE_DIR` 会解析为 `APP_DATA_DIR` 下的路径；绝对路径也必须位于 `APP_DATA_DIR` 内。该目录应视为管理员私有数据。

本阶段没有把任何实时外部公告附件下载进仓库，也没有虚构招聘岗位。自动化测试使用本地临时 XLSX 和受控假响应，只验证处理机制；真实来源仍须逐站确认公告页、附件 URL、robots 和字段证据后再处理。

## 审阅顺序

1. 阅读 [数据迁移说明](MIGRATION_NOTES.md)，确认 SQLite 和运行目录影响。
2. 阅读 [质量报告](QUALITY_REPORT.md)，确认测试、边界和“没有新增真实岗位”的事实。
3. 阅读 [交付说明](DELIVERY_NOTES.md)，核对 API、截图和已知限制。
4. 在本地用一个已核验的官方公告做人工演练，再决定是否进入 Phase 3 单位矩阵扩展。

## 版本边界

本阶段从已发布的 `v0.5.0` 创建独立分支 `phase/2-official-attachments`。得到审阅通过前，不合并到 `master`，不创建 `v0.6.0`，也不启用大规模附件同步。
