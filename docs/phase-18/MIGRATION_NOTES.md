# Phase 18 迁移说明

## 数据与配置

- `data/sources.json` 的 `sinopec-career` 改为 `sinopec_spa_rows`。
- 新增 `data/verified/sinopec-geoscience-20260924.json`。
- 来源仍为停用状态；不会改变现有学生端岗位数据。

## 代码

- `job_hub/sinopec.py`：捕获文件契约、单位状态和摘要统计。
- `job_hub/sources.py`：将捕获岗位转换成统一 `RawPosting`。
- `job_hub/contracts.py`：注册新的来源类型和配置约束。
- `job_hub/cli.py`：增加 `sinopec-capture` 运维命令。

## 回滚

回退到 `v0.17.0` 即可移除本阶段代码和快照；本阶段未执行数据库结构迁移。
