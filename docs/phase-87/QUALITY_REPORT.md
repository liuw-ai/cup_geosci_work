# Phase 87 质量报告

## 变更验证

```text
python -m pytest -q tests/test_government_position_cli.py tests/test_government_positions.py tests/test_government_revalidation.py tests/test_government_position_publish.py
28 passed
```

全量回归：

```text
401 passed, 1 skipped
```

另行通过：

- `python -m compileall -q job_hub`
- `git diff --check`
- `data/sources.json`、`data/government_position_registry.json` JSON 校验
- 两份 Docker Compose 配置校验

## 影响范围

本阶段不增加岗位数，也不把历史公务员职位表导入学生端。它修复的是每日复核状态在审计入口和生产入口之间不一致的风险，使来源故障、官方撤回、截止清退能够在管理员报告中得到与学生端发布相同的结论。

## 当前限制

服务器尚未部署本阶段；`jobs.cupdky.cn` 仍受 DNS 配置限制。部署前需按既定备份流程保存数据库，再执行一次审计命令确认运行复核记录已被读取。
