# Phase 81 质量报告

## 自动化验证

- `python -m pytest tests/test_attachment_batch.py -q`：2 passed
- 全量测试：见阶段交付记录
- `python -m compileall -q job_hub`：通过

覆盖点：

- `registered`、`downloaded` 两类未完成附件会被处理；
- 默认不重试 `failed` 和 `skipped`，避免把访问受限误报为无岗位；
- `--retry-failed` 只重试明确失败状态，并将异常保留在返回结果；
- 处理顺序和来源过滤可审计；
- 候选仍停留在私有复核队列。

## 验收边界

本阶段只验证队列调度和门禁，不声称新增岗位数量。真实新增岗位必须在服务器受控下载、逐行专业/学历/地点/人数/截止日期核验并人工发布后才能计入公开数量。
