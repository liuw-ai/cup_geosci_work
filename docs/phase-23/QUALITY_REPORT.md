# Phase 23 Quality Report

生成日期：2026-09-25（Asia/Shanghai）  
分支：`phase/23-government-position-table-expansion`

## 自动化验证

```text
python -m pytest -q
234 passed in 10.52s
```

```text
python -m job_hub.cli government-position-audit --today 2026-09-25
```

审计结果：

| 指标 | 结果 |
| --- | ---: |
| 官方来源状态台账 | 3 |
| 逐岗位记录 | 2 |
| 事业单位逐岗位记录 | 2 |
| 公务员逐岗位记录 | 0 |
| 当前 `verified_open` | 0 |
| 专业明确但待补证据 | 2 |
| 官方公告 URL 完整率 | 100% |
| 官方附件 URL 完整率 | 100% |
| 专业/学历字段完整率 | 100% |
| 地点字段完整率 | 0% |
| 截止日期完整率 | 100% |

地点完整率为 0% 是真实缺口：安徽岗位表行未单列地点，所以两条记录被保留在人工复核队列，而不是继续展示给学生。该结果证明门禁有效，不能解读为安徽没有招聘。

对现有 SQLite 运行库执行 `python -m job_hub.cli reindex-jobs` 后，596 条历史岗位中 146 条派生字段被重新分类；学生端在招数由 123 条校正为 121 条，减少的 2 条正是缺少政府岗位地点证据的安徽记录。没有删除原始记录，后续补齐证据仍可恢复。

## 真实来源样例

- 安徽省地质矿产勘查局公告：<https://dkj.ah.gov.cn/xwzx/tzgg/40788529.html>
- 安徽官方岗位表：<https://dkj.ah.gov.cn/group3/M00/14/5E/wKg86mnx3JSAe_TTAAA0PEnJC-Q385.xls>
- 国家公务员局官方入口：<https://bm.scs.gov.cn/>

以上链接均作为官方证据或官方入口保存；中公、华图、公众号未进入学生端证据链。
