# Quality Report

## Automated checks

- Full test suite: `293 passed`
- Government audit date: `2026-09-27`
- Government registry records: `122` (`113` public-institution, `9` postdoctoral)
- Explicit student matches: `122`
- Fixed-deadline records: `81`
- Open-until-filled records: `41`
- Required evidence fields complete: `100%` for position code, major, degree, location, official notice, official attachment and row locator.
- Source failures represented separately: `0` in the publishable registry; unavailable sources remain in `source_assessments` and are not treated as “no jobs”.
- 甘肃省地矿局来源已绑定 `gansu-geology-bureau` 并启用正式栏目扫描；岗位不再来自停用来源。
- 中国地震局2027年度事业单位公开招聘：70条明确匹配岗位、89个计划名额；服务器直连下载官方 Excel 成功，SHA-256 为 `646e153605e06596c21601695b1072ba1a74a8f24ec18321fd54b3d184589729`。
- 中国地震局岗位均保留官方公告、官方 Excel 和 `附件1《岗位信息表》Excel第{N}行（A{N}:M{N}）` 证据定位；合并单位名称已向下继承，地点优先取岗位备注/岗位名称。

## Manual evidence checks

- 湖北公告：`http://zrzyt.hubei.gov.cn/fbjd/xxgkml/zkly/202604/t20260423_5921057.shtml`
- 湖北附件：`http://zrzyt.hubei.gov.cn/fbjd/xxgkml/zkly/202604/P020260423528630344561.xlsx`
- 甘肃调整公告：`http://gsdkj.net/xxgk/rsxx/content_103876`
- 甘肃附件：`http://gsdkj.net/upload/main/contentmanage/article/file/2026/09/08/202609080914513449.pdf`
- 中国地震局公告：`https://www.cea.gov.cn/cea/zwgk/rsxx/ryzp/5855536/index.html`
- 中国地震局附件：`https://www.cea.gov.cn/cea/zwgk/rsxx/ryzp/5855536/2026092316044245325.xlsx`

The 湖北 attachment has seven rows; only row 2 is in scope. The 甘肃 attachment has two rows; both are in scope. Closed provincial notices and historical civil-service tables were excluded.
