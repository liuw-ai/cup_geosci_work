# Migration Notes

1. 备份运行库：`runtime/job_hub.pre-phase15-20260924.sqlite3`。
2. 运行 `python -m job_hub.cli reindex-jobs`：只重算派生字段，不会凭空生成岗位级证据；无证据旧记录被降级。
3. 对官方页面 `460401` 使用当前适配器重新解析并写入 12 条独立岗位行。
4. 原公告级记录 `275` 通过 `superseded` 事件保留历史可追溯性，不删除数据。
5. 低于来源 `minimum_relevance` 的 4 条重建行标记为 `filtered`，保留 `filtered` 事件，不进入公开在招列表。

生产环境升级时，应先复制 SQLite 文件并停止 worker，再初始化代码版本；不要在数据库正在写入时覆盖运行库。无法重新访问官方原文的历史岗位不能自动“补证据”，应进入人工复核队列。
