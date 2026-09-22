# 国内官方来源证据

## 中国冶金地质总局地球物理勘查院

- 官方原文：<https://www.geoexp.cn/contact/view_272.html>
- 官方主页：<https://www.geoexp.cn/>
- 组织归属：中国冶金地质总局体系，地球物理与资源勘查研究/技术单位
- 采集方式：公开 HTML 公告；按明确岗位标题段落拆分
- 本轮发现：6 条专业相关岗位
- 典型专业证据：物探、地震、矿床地质、地质大数据、遥感地质、测绘工程、地理信息
- 证据规则：6 条记录共享同一 `source_url` 和 `official_evidence_url`，依靠独立 `external_id` 区分岗位
- 离线夹具：`tests/fixtures/domestic/geoexp_recruitment.html`
- 回归测试：`tests/test_structured_openings.py::test_official_role_split_notice_creates_one_record_per_matching_role`

## 中国石油大学（北京）就业信息网

- 官方招聘栏目：<https://career.cup.edu.cn/campus>
- 官方学院发布栏目：<https://career.cup.edu.cn/news/index/tag/xwzp>
- 采集方式：列表页只作公开公告发现；详情页读取正文、表格和官方发布时间/过期时间。
- 本轮新增在招：2 条（延长壳牌（四川）有限公司、赣南实验室）；另记录 3 条已截止官方历史公告。
- 适配要点：候选窗口不再在前 28 个卡片处截断；正文中的“通知/活动”等门户导航词不再误删真实公告；招聘账号显示为人力资源服务机构时，单位字段回到公告标题中的实际用人单位。
- 证据规则：岗位 `source_url` 与 `official_evidence_url` 均指向 `career.cup.edu.cn` 原文；学校转载页不能替代单位官网证据，但可以作为高校官方招聘公告来源单独展示。
- 回归测试：`tests/test_sources.py::test_cupb_adapter_scans_past_irrelevant_listing_cards`、`test_cupb_adapter_keeps_free_form_major_evidence_and_real_employer`

## 安徽省地质矿产勘查局

- 官方公告：<https://dkj.ah.gov.cn/xwzx/tzgg/40788529.html>
- 官方职位表：<https://dkj.ah.gov.cn/group3/M00/14/5E/wKg86mnx3JSAe_TTAAA0PEnJC-Q385.xls>
- 文件特征：扩展名为 `.xls`，实际内容为 OOXML；处理器通过文件头识别并使用 `openpyxl`，同时保留原始哈希和 URL
- 结果：22 行提取；2 行完成人工核验并发布，20 行仍为 `needs_review`
- 不公开原因：职位表含机械、计算机、财务等非目标专业，不能按单位名称推断专业资格
- 回归测试：`tests/test_attachments.py::test_mislabelled_xls_with_ooxml_content_uses_excel_parser`

## 中国冶金地质总局与中国煤炭地质总局

- 中国冶金地质总局招聘栏目：<https://www.cmgb.com.cn/category/zpxx.html>
- 中国煤炭地质总局招聘栏目：<https://www.ccgc.cn/rlzy/rczp.htm>
- 本轮均能完成公开栏目扫描并发现真实公告，但当前发现的部分报名截止日期早于 2026-09-23，因此只保留为历史记录，不计入在招岗位。

## 访问受限入口

中国石油统一招聘入口返回 HTTP 412，中国石化入口的 robots 返回 403，国家管网招聘子域的 robots 返回 403。系统记录这些状态并停止访问，不把它们写成“无岗位”；后续优先从其下属单位官网和公开职位表补齐。
