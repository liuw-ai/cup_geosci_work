# Phase 17 数据迁移说明

本阶段没有新增数据库表和破坏性迁移。岗位导入继续复用 `jobs`、`job_events` 及现有派生字段；运行时 SQLite 文件不提交到 Git。

## 服务器部署

1. 更新代码后执行 `python -m job_hub.cli init`（幂等）。
2. 导入经过人工核验的快照：

   ```powershell
   python -m job_hub.cli import-json data/verified/cnpc-geoscience-20260924.json
   python -m job_hub.cli import-json data/verified/domestic-geoscience-20260924.json
   ```

3. 重算发布派生字段并审计：

   ```powershell
   python -m job_hub.cli reindex-jobs
   python -m job_hub.cli audit
   python -m job_hub.cli coverage --output runtime/phase17-final-coverage.json --record
   ```

4. 日报任务继续使用现有 20:00 调度；不需要手动修改数据库结构。

## 来源队列

`data/domestic_source_expansion_queue.json` 是管理员扩源台账，不是岗位表。`official_job_sample_verified` 仅表示已找到岗位级样例；`scan_success_no_match` 表示栏目可访问且扫描成功但观察窗口内没有符合专业边界的岗位；`access_limited` 表示访问受限。两种状态都不能被学生端当作岗位或“无岗位”展示。
