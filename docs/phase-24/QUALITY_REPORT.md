# Phase 24 Quality Report

日期：2026-09-25  
分支：`phase/24-provincial-position-table-import`

## 网络复测

| 来源 | 本机结果 | 结论 |
| --- | --- | --- |
| 山东人事考试信息网 | HTTP 403 | 访问受限，不能解释为无岗位 |
| 山东省地矿局 | HTTP 502 | 当前网络/入口故障，不能解释为无岗位 |
| 河南省地质局 | HTTP 403 | 访问受限，不能解释为无岗位 |
| 天津规划和自然资源局 | TLS EOF | 当前网络故障，不能解释为无岗位 |
| 国家公务员局 | TLS EOF | 当前网络故障且年度职位表未确认，不能生成岗位 |

## 已登记官方附件

| 省份 | 公告 | 附件 | 报名截止 | 当前状态 |
| --- | --- | --- | --- | --- |
| 山东 | [官方公告](http://dkj.shandong.gov.cn/art/2026/7/17/art_356047_10333801.html) | [岗位表](http://dkj.shandong.gov.cn/module/download/downfile.jsp?classid=0&filename=732c4f35c08742398d1c946003273bca.xlsx) | 2026-07-28 | 历史关闭 |
| 河南 | [官方公告](https://dzj.henan.gov.cn/2026/04-22/8190.html) | [岗位表](https://dzj.henan.gov.cn/file/file/xlsx/2026/04/22/5ee1f4c87add4b50a0548f717c50977c.xlsx) | 2026-04-30 | 历史关闭 |
| 天津 | [官方公告](https://ghhzrzy.tj.gov.cn/zwgk_143/zfxxgk1/fdzdgknr1/zkly/202602/t20260228_7252855.html) | [岗位表](https://ghhzrzy.tj.gov.cn/zwgk_143/zfxxgk1/fdzdgknr1/zkly/202602/W020260228691245270225.xls) | 2026-03-25 | 历史关闭 |

## 自动化验证

```text
python -m pytest -q tests/test_government_artifacts.py tests/test_government_positions.py tests/test_attachments.py
15 passed
```

全量测试和发布前审计将在提交前再次执行。当前没有新增学生端岗位，这是因为三份附件报名期已结束且本机无法下载验证；不把历史附件冒充当前岗位是本阶段的正确结果。
