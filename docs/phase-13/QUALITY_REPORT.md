# Quality Report

## Automated checks

- `pytest -q`: 178 passed
- `git diff --check`: passed
- Python import/compile checks: passed

## Data correctness rule

岗位级专业要求必须来自同一职位行、职位详情字段或明确资格段落。公司简介、行业介绍、岗位职责中的地学词汇只能作为发现信号，不能单独生成“明确匹配”。

## Remaining work

当前真实数据库仍需备份后重采 CUPB 岗位；三桶油下属单位、31 省事业编/公务员职位表和附件队列仍未达到全国覆盖验收标准。本阶段不宣称岗位数量目标已经完成。
