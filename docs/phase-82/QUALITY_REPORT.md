# Phase 82 质量报告

## 自动化验证

- `python -m pytest tests/test_cmgb_browser_worker.py -q`：2 passed
- 全量测试：阶段验收时执行
- `python -m compileall -q job_hub`：通过

## 发布边界

超时只生成 `parse_failed` 失败记录并保留旧成功快照；不会把部分页面、空结果或失败结果当作当前无岗位，也不会自动增加学生端岗位数。
