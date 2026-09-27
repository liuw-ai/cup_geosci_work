# Phase 54 Quality Report

## Local verification

- `python -m pytest -q`: **307 passed**.
- 新增真实 DOM 夹具覆盖：标题、单位、地点、学历、人数、截止日期和职位介绍专业提取。
- 新增门禁覆盖：只有“详见职位描述”而没有专业正文时，解析失败，不发布。
- 新增回归覆盖：`official_cmgb_browser_detail` 详情证据经过正常学生端专业门禁。

## Server verification

服务器捕获结果以最终文件 `/opt/cup_geosci_phase54/captures/cmgb-iguopin-browser.json` 为准。报告必须填写：

| 指标 | 结果 | 发布要求 |
|---|---:|---|
| 扫描页数 | 8 | 分页完成 |
| 发现详情数 | 146 | 与列表扫描一致 |
| 成功详情数 | 146 | 等于发现数 |
| 失败详情数 | 0 | 必须为 0 |
| 导出岗位数 | 146 | 等于发现数 |
| 专业/学历/地点/人数/截止字段完整率 | 100% (146/146) | 100% |
| 进入隔离内部库 | 77 | 其余 69 条低于来源相关性阈值，未写入岗位库 |
| 学生端明确匹配数 | 16 | 逐条有专业和学历证据 |
| 专业不匹配 | 55 | 不得发布到学生端 |
| 相关专业待核验 | 6 | 不得伪装成明确匹配 |

捕获文件：`runtime/cmgb-capture-server.json`（服务器原件为
`/opt/cup_geosci_phase54/captures/cmgb-iguopin-browser.json`）。上述发布分类来自
隔离 SQLite 管线演练，未修改旧生产数据库。现有 33 条人工核验快照仍是生产来源；动态来源在完成去重替换和服务器定时任务验证前保持停用，避免重复岗位和一次性快照被误当作持续更新。
