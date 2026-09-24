# Phase 20 迁移说明

## 代码

- `job_hub/sources.py`：从中国石油详情 URL 提取内部 `recruitId`，写入岗位证据；补充官方列表入口和详情接口状态。
- `job_hub/pipeline.py`：让采集和人工快照导入共享同一套 CNPC 证据补全逻辑。
- `job_hub/templates/job_detail.html`：对 CNPC 详情接口异常显示透明提示，主链接指向官方招聘列表。

## 数据库

没有结构迁移。重新运行中国石油同步或重新导入快照即可补齐历史岗位的详情编号和状态字段：

```text
python -m job_hub.cli sync-source cnpc-career
```

## 回滚

回退到 `phase-19-review` 或提交 `1c65fde` 即可移除本阶段展示和证据字段改动；岗位数据本身不会被删除。
