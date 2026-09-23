# Migration Notes

## Code and data

- `data/major_taxonomy.json` 是只增量读取的版本化注册表，不修改 SQLite 模式。
- `job_hub/profiles.py` 仍输出原有七个画像 ID，现改为从专业注册表加载精确/相邻术语，因此旧 URL 和筛选参数保持兼容。
- `job_hub/matching.py` 移除裸 `地质资源` 关键词，并遮蔽嵌套短语中的 `地质工程`。
- `data/sources.json` 新增 `cosl-career`，默认 `enabled=false`；`data/organization_registry.json` 将其绑定到中海油服官方 ATS 频道。

## Runtime data

执行重索引前已备份：

```text
runtime/job_hub.pre-phase14-20260924.sqlite3
```

本阶段重索引结果：`checked=162`，`reclassified=40`，`normalized=0`，`unchanged=122`。运行库中没有写入任何虚构岗位；运行时数据库和备份均被 `.gitignore` 排除，不进入 Git。

回退时停止 worker，恢复上述 SQLite 备份或切换到本阶段前的 Git 提交；不要删除运行目录中的其他日报和附件。

