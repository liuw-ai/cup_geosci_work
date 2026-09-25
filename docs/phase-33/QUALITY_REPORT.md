# Phase 33 质量报告

日期：2026-09-25  
分支：`phase/33-cnpc-browser-index`

## 官方浏览器捕获

| 指标 | 结果 |
| --- | ---: |
| 官方列表页扫描 | 13/13 |
| 公告索引观察 | 122 |
| 重点详情目标 | 3 |
| 详情字段完整并可发布 | 0 |
| 详情接口退化 | 3 |
| 本阶段新增学生端岗位 | 0 |

勘探开发研究院和中油测井公司详情页均触发官方前端 `recruitInfoshow.js` 的 `recruitInfoObj` 缺失错误。该结果说明公告存在，不说明岗位字段可见，更不说明无岗位。

## 事业编/公务员

- 安徽省地矿局官方公告：安徽工业经济职业技术学院 2026 年高层次人才招聘 29 人，报名截止 2026-10-31；岗位表附件已登记为服务器受控下载。
- 当前岗位台账中 2 条安徽地质相关博士岗位仍为 `manual_review_required`，地点字段完整率 0%，因此明确匹配公开数仍为 0。
- 国家公务员官方职位表尚未确认 2027 年正式发布，继续保持入口待复核，不导入历史表充数。

## 自动化验证

```text
python -m pytest -q
257 passed

python -m job_hub.cli cnpc-browser-capture
status=partial; pagination_complete=true; announcements_discovered=122; publishable_job_rows=0

python -m job_hub.cli audit
ok=true; issues=[]
```

