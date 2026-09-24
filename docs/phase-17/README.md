# Phase 17：国内官方来源扩展与岗位级核验批次

## 目标

本阶段针对国内来源集中度高的问题，完成两件可回退、可审计的工作：

1. 建立三桶油、国家管网、自然资源/地勘、事业编和公务员的官方来源扩展队列，明确主体、上级体系、官方入口、备用入口、当前证据状态和下一步动作。
2. 从中国石油官方招聘系统核验 20 条岗位级地学机会，覆盖 10 个下属油田，补齐专业、学历、地点、截止日期和岗位行证据，经过现有学生端门禁后发布。

队列不是岗位数据。`official_identity_only`、`access_limited` 和 `manual_review_required` 只能作为管理员任务，不能被解释为“无岗位”。

## 真实来源样例

- [大庆油田油气田勘探开发技术研究岗位](https://career.cup.edu.cn/campus/view/id/460397)：公告原文明确按学历层次列出地质工程、地质资源与地质工程、地球物理学等专业。
- [中国电建集团河北省电力勘测设计研究院岩土勘察工程师](https://career.cup.edu.cn/campus/view/id/460416)：公告原文明确硕士研究生、地质工程。
- [中国石油高校毕业生招聘平台](https://zhaopin.cnpc.com.cn/web/recruitInfolist.html)：本批通过浏览器可达的官方详情页核验 20 条岗位，覆盖大庆、辽河、长庆、塔里木、新疆、西南、吉林、大港、青海、华北 10 个油田/油气田分公司；每条岗位保留详情页、专业、学历、地点和 2026-10-15 截止日证据。
- [中国石油集团东方地球物理勘探有限责任公司官方招聘页](https://www.bgp.com.cn/bgpen/Recruitment/first_common2023hr.shtml)：已在国家能源矩阵中登记，社会招聘岗位仍按应届适配门禁处理。
- [中国石化石油勘探开发研究院公告](http://pepris.sinopec.com/pepris/careers/rwzl/2026/6/I1519026538069098496.shtml)：岗位级字段已核验，但该批报名截止日为 2025-11-15，不计入当前在招。
- [安徽省地质矿产勘查局高层次人才公告](https://dkj.ah.gov.cn/xwzx/tzgg/40788529.html)：已有官方附件流水线；当前只有逐行核验的地质学/地质资源与地质工程岗位进入学生端。

## 代码与数据变更

- `data/domestic_source_expansion_queue.json`：23 条国内官方扩源任务，覆盖中国石油（10 个油田独立登记）、中国石化、中国海油、国家管网、省级事业编、自然资源/地勘和公务员。每条都有备用入口和状态；`scan_success_no_match` 与 `access_limited` 明确区分。
- `job_hub/domestic_expansion.py`：队列加载、URL/状态校验、按体系或状态筛选和聚合统计；不执行网络请求。
- `job_hub/cli.py`：新增 `domestic-expansion-queue` 管理命令。
- `data/verified/domestic-geoscience-20260924.json`：两条岗位级官方快照，复用现有 `import-json`、专业匹配和发布审计。
- `tests/test_domestic_expansion.py`：覆盖队列契约、快照发布门禁和非法状态拒绝。

数据库没有新增表，也没有迁移。运行时导入只会新增/更新普通 `jobs`、`job_events` 和日报所需派生字段；附件私有目录、候选池和学生端接口契约不变。

## 使用

```powershell
python -m job_hub.cli domestic-expansion-queue
python -m job_hub.cli domestic-expansion-queue --system 中国石油
python -m job_hub.cli domestic-expansion-queue --status access_limited
python -m job_hub.cli import-json data/verified/domestic-geoscience-20260924.json
python -m job_hub.cli reindex-jobs
python -m job_hub.cli audit
python -m job_hub.cli coverage --output runtime/phase17-coverage.json --record
python -m job_hub.cli simulate-cohort
```

## 质量结果（2026-09-24）

- 原始岗位记录：226 -> 248（新增 22 条岗位级快照，其中中国石油官方平台 20 条、其他官方高校/单位 2 条）。
- 学生端公开在招：32 -> 54。
- 中国石油快照：20 条，覆盖 10 个下属油田/油气田单位；每条 `field_evidence["岗位"]` 与岗位标题一致。
- 新增岗位：均为 `student_eligible`，均有官方原文 URL、岗位级 `official_html_table_row` 证据、专业、学历、地点和截止日期。
- `audit`：`ok: true`，无公开岗位审计问题。
- 100 人模拟：所有画像仍有明确匹配；新增岗位覆盖硕士/博士地质工程、地质资源与地质工程方向。
- 扩源队列：23 条记录，100% 有备用入口；其中 14 条已有岗位样例，1 条为成功扫描但当前无匹配，4 条明确记录为访问受限，1 条为公务员职位表解析待人工复核。
- 来源集中度最高：国家管网 21/54（38.89%），从上一基线的 61.76% 降至 `watch`；中石化、中海油和省级事业编仍需继续逐来源扩展。

浏览器快照与普通 HTTP/TLS 的边界：本批中国石油详情页在浏览器直连环境可访问并完成人工核验，但普通 `curl`/requests 仍可能返回 TLS EOF 或 HTTP 412；因此不能把普通 HTTP 失败当成官方无岗位。运行时保留历史失败抓取记录，快照只在字段证据和发布审计通过后入库。

当前仍未完成的工作：三桶油统一招聘入口、海油服 ATS、自然资源部/地调局及 31 省职位表的自动化访问仍受 TLS、robots 或动态接口限制。下一阶段必须在部署服务器复测，并优先处理队列中的 `access_limited` 和 `manual_review_required`，不能把队列条数或来源登记数当作岗位数。
