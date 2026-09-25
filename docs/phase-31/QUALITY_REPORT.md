# Phase 31 质量报告

日期：2026-09-25  
分支：`phase/31-sinopec-live-capture`

## 自动化验证

```text
python -m pytest -q
246 passed

python -m compileall -q job_hub
passed

python -m job_hub.cli sinopec-capture \
  --path data/verified/sinopec-geoscience-20260925.json \
  --require-complete --require-scan-complete
通过：132 个单位、35 个候选单位、397 条岗位行、失败行 0
```

## 专业和发布验证

```text
中石化来源同步：发现 397 条
明确学生端匹配：75 条
待核验/不适配岗位：保留管理员审计库
数据库审计：ok=true，issues=[]
模拟地学院 100 人：100 人有明确岗位推荐，0 人无推荐
```

## 重要限制

本阶段捕获来自公开浏览器页面，快照已部署到服务器并由 Worker 正常同步。由于服务器端普通请求仍受到 robots 403 和动态会话限制，尚未完成服务器无人值守浏览器的每日自动刷新。因此当前结论是“官方快照采集和服务器发布链路通过”，不是“中石化已经自动实时更新”。
