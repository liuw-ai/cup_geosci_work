# Phase 12 交付说明

## 变更摘要

1. `job_hub/sources.py`：完善 CUPB 压缩正文、分页、候选优先级、标题单位识别和正文地点证据解析。
2. `data/sources.json`：为 CUPB 登记四个已核验官方详情页备用入口，最低相关度阈值调整为 37；低于明确地学匹配的岗位仍标记为“相关机会”。
3. `tests/test_sources.py`、`tests/test_cupb_current.py`：增加真实页面夹具和单位/地点回归测试。
4. 运行时数据库：修正赣南实验室、桂林理工大学和福建省能源石化创新研究院的单位/地点字段；运行时 SQLite 不作为 Git 版本文件提交。

## 真实来源样例

- [赣南实验室 2026 年招聘公告](https://career.cup.edu.cn/campus/view/id/460487)
- [桂林理工大学 2026 年招聘公告](https://career.cup.edu.cn/campus/view/id/460426)
- [福建省能源石化创新研究院有限责任公司](https://career.cup.edu.cn/news/view/aid/95612/tag/xwzp)
- [广西北部湾投资集团 2027 届校园招聘公告](https://career.cup.edu.cn/campus/view/id/460523)

以上链接均来自 CUPB 公开就业信息栏目，学生端保留原文地址；中公、华图和微信公众号不作为学生端证据。

## 测试结果

- `177 passed`
- `python -m compileall -q job_hub tests` 通过
- 数据库审计：`ok=true`，`open_jobs=90`，`issues=[]`
