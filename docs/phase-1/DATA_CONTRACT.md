# Phase 1 数据契约

## 公开与私有边界

学生端只读取 `jobs` 中已经发布的岗位，并继续显示单位官网、政府官网、高校官网、科研院所官网或正式招聘系统的原始链接。`source_artifacts`、`job_evidence` 和 `candidate_lead_events` 是管理员审计数据，绝不出现在公开网页、日报、`/api/jobs`、搜索结果或学生画像结果中。

私有线索可以来自公众号、教辅机构、行业媒体或招聘平台，但它们只能作为发现入口。只有找到并核对正式原文，才会经过受保护的发布流程写入 `jobs`。

## 来源注册表

每个来源至少需要：

| 字段 | 约束 | 用途 |
| --- | --- | --- |
| `id` | 全局唯一、非空 | SQLite 与配置中的稳定标识。 |
| `name`、`publisher`、`category` | 非空 | 向管理员解释来源主体和岗位分类。 |
| `homepage_url` | 完整 HTTP(S) URL | 官方公开入口。 |
| `source_type` | 仅允许已实现的适配器类型 | 防止配置一个不存在的采集器。 |
| `source_tier` | `A` 或 `B` | 保留既有官方/高校来源质量分级。 |
| `config` | JSON 对象 | 适配器的白名单、频率和选择器配置。 |
| `enabled` | 布尔值 | 仅启用来源才允许 worker 同步。 |

历史类别字符串不会被本契约强制重命名，避免错误重写已有岗位；展示分类仍由现有单位和岗位分类模块规范化。

## 单位注册表

每个单位必须具有稳定 `id`、标准名称、岗位类别、单位类型、所属体系、非空别名列表和非空官方域名列表。子单位还必须满足：

- `parent_id` 指向已存在的单位，且不能指向自己；
- `parent_name` 必须与父单位的标准名称一致；
- 父子关系不能形成循环；
- 别名不能为空，官方域名必须是有效域名。

这使“中国石油勘探开发研究院”“长庆油田”“中油测井”“中海油服”“石化油服”等集团内单位可以和上游运营主体、独立油服、国际油服区分，而不是统称为含混的“三桶油与油服”。

## 官方附件台账：`source_artifacts`

附件台账在 Phase 1 只登记元数据。文件不会被本阶段下载或写入磁盘。

| 字段 | 说明 |
| --- | --- |
| `source_id` | 关联的已登记官方来源。 |
| `parent_url` | 发现附件的官方公告页。 |
| `artifact_url` | 附件公开 URL。 |
| `artifact_kind` | `announcement_attachment`、`position_table`、`application_material` 或 `supporting_document`。 |
| `media_type` | 可选 MIME 类型，例如 `application/pdf`。 |
| `content_sha256` | 受控下载后才填写的 SHA-256；本阶段通常为空。 |
| `storage_path` | 未来受控存储中的相对路径，不允许绝对路径或目录穿越。 |
| `parser_version`、`extraction_status` | 为 Phase 2 的解析与重跑预留。 |
| `metadata_json` | 附件显示名、公告日期等非敏感元数据。 |

## 岗位证据：`job_evidence`

一个岗位可有多个证据，均保留 URL、定位信息、摘录、验证状态和时间戳。支持的 `evidence_type`：

- `official_page`：正式公告页面；
- `official_record`：正式招聘系统中的具体职位记录；
- `attachment`：公告附件或职位表；
- `field_excerpt`：从已登记官方页面或附件中定位到的字段摘录。

公开岗位的发布审计要求至少存在一条 `official_page` 或 `official_record`，且 `verification_status` 为 `verified`。附件证据可以仍是 `pending`，但不能替代官方原文页面。

若证据绑定了 `artifact_id`，该附件必须和岗位属于同一个 `source_id`。这能防止将 A 单位的职位表错误关联到 B 单位的岗位。

## 私有线索状态机

```text
candidate
  -> official_url_found
  -> official_content_verified
  -> published

candidate / official_url_found / official_content_verified
  -> need_review / rejected / expired

need_review
  -> candidate / official_url_found / official_content_verified / rejected / expired
```

- `official_url_found`：必须有完整 `official_url`。
- `official_content_verified`：必须有官方 URL 和可读的核验说明。
- `need_review`：必须记录下一步需要完成的核验说明。
- `published`：只能由专用发布动作从 `official_content_verified` 进入。
- `published`、`rejected`、`expired` 为终态，只允许原状态更新，不允许重新跳转。

每次状态变化写入 `candidate_lead_events`；老线索在首次迁移时会拥有一个状态快照事件。

## 管理员接口

所有以下接口都要求 `X-Admin-Token`，没有任何学生端入口。

| 接口 | 作用 |
| --- | --- |
| `GET/POST /api/admin/artifacts` | 查询或登记官方附件元数据。 |
| `GET/POST /api/admin/jobs/<job_id>/evidence` | 查询或添加一个岗位的证据记录。 |
| `GET /api/admin/leads/<lead_id>/events` | 查看私有线索的状态历史。 |
| `POST /api/admin/jobs` | 可选传入 `official_evidence_url`，否则回退到 `source_url`。 |

这些接口不下载文件、不调用外部网站，也不改变公开岗位筛选规则。
