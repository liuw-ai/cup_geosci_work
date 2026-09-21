# Phase 5：私有发现线索与官方核验流

## 目标

本阶段解决“官方来源不容易被统一检索，国内岗位容易漏掉”的发现问题，同时保留学生端的真实性边界：中公、华图、国聘、应届生、石油/地质垂直平台和微信公众号只能帮助管理员发现线索，不能成为学生端的最终来源。

服务对象仍是中国石油大学（北京）地球科学学院：资源勘查工程本科生，以及地质学、地质工程、地质资源与地质工程硕士生和博士生。

## 已交付

- `data/discovery_sources.json`：9 个私有发现渠道的版本化注册表，记录入口、用途、访问方式、最近检查日期和当前访问状态。
- `job_hub/discovery.py`：发现渠道契约读取、线索 URL 规范化、稳定指纹、官方域名评估和转化漏斗统计。
- `candidate_leads` 加法迁移：增加发现来源、线索指纹、官方来源、官方域名状态和核验时间字段。
- `candidate_lead_mentions`：同一公告被多个发现渠道提及时保留每个渠道的私有痕迹，不在岗位表中制造重复记录。
- 管理员接口：
  - `GET /api/admin/discovery-sources`
  - `GET /api/admin/discovery-funnel`
  - `GET /api/admin/leads/<id>/mentions`
  - `/api/admin/leads` 支持按发现来源和省份筛选。
- CLI：
  - `python -m job_hub.cli discovery-sources`
  - `python -m job_hub.cli discovery-funnel`
  - `python -m job_hub.cli list-leads --discovery-source-id ... --province ...`
- 公开接口、公开网页、日报和学生端岗位库没有新增任何第三方字段或第三方链接。

## 发现入口登记

本批登记的入口是用户提供的公开平台和公众号渠道：中公教育、华图教育、国聘网、应届生求职网、石油英才网、中国地质人才网、阿果石油英才网、矿业人才网，以及微信公众号人工白名单入口。

登记不等于自动采集。2026-09-21 的本地只读连通性检查结果如下：

| 渠道 | 当前状态 | 说明 |
| --- | --- | --- |
| 华图教育 | `checked` | 当前网络可返回首页；只作发现线索 |
| 中公教育、国聘、应届生、石油英才、中国地质人才、阿果石油、矿业人才 | `access_limited` | TLS、403、网关或访问策略问题，未绕过限制 |
| 微信公众号白名单 | `registered` | 只登记人工核验规则，未伪造账号或文章数据 |

这里的状态描述“当前检查结果”，不是平台本身是否可靠的最终评价。访问受限时不会自动写成“无岗位”。

## 线索状态和发布门槛

```text
第三方发现入口
    -> candidate
    -> official_url_found
    -> official_content_verified
    -> published
```

线索的 `lead_url` 永远是私有发现证据，不能作为公开岗位原文。发布前必须满足：

1. `official_url` 是单位官网、政府官网、高校/科研院所官网或正式招聘系统原文。
2. 官方链接域名与已登记来源匹配，或管理员显式设置 `official_domain_status=manual_review_approved` 并留下核验说明。
3. 岗位标题、单位、专业、学历、地点和截止日期按已有岗位契约核对；缺失字段必须明确标为未注明，不能猜测。
4. 通过现有发布前审计后，才由管理员发布到 `jobs`；第三方线索不会自动发布。

同一规范化 URL只形成一个线索指纹；同一公告从多个渠道发现时，`candidate_lead_mentions` 保留渠道归因，但学生端只会有一条正式岗位记录。

## 管理员查看

接口均需要 `X-Admin-Token`，不向学生端公开：

```text
GET /api/admin/discovery-sources
GET /api/admin/discovery-funnel
GET /api/admin/leads?discovery_source_id=huatu-discovery&province=山东
GET /api/admin/leads/<lead_id>/mentions
```

CLI 只读私有台账和数据库，不触发网络抓取。`discovery-funnel` 的数量是“线索转化数量”，不能当作在招岗位数量或就业结果。

## 不在本阶段宣称完成

- 没有对微信公众号、聚合平台或受限站点绕过登录、验证码、robots、TLS 或访问控制。
- 没有把第三方页面直接展示给学生，也没有新增虚构岗位。
- 没有完成全国第三方渠道自动监控；公众号需要后续人工白名单、官方账号身份和授权访问方式逐个登记。
- 没有把未登记官方域名自动判定为可信；未登记但确属官方的链接必须走管理员人工批准。
- 没有改变学生端页面布局或公开 API 的语义。

## 后续阶段边界

下一阶段优先做学生端筛选、查询性能和管理员观测（Phase 6），再把已核验的线索转化率与省份/单位覆盖质量纳入日报。具体官方来源仍按 Phase 3、Phase 4 的逐站准入和离线夹具要求接入。
