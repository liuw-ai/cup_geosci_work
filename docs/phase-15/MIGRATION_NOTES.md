# Migration Notes

1. 备份运行库：`runtime/job_hub.pre-phase15-20260924.sqlite3`。
2. 运行 `python -m job_hub.cli reindex-jobs`：只重算派生字段，不会凭空生成岗位级证据；无证据旧记录被降级。
3. 对官方页面 `460401` 使用当前适配器重新解析并写入 12 条独立岗位行。
4. 原公告级记录 `275` 通过 `superseded` 事件保留历史可追溯性，不删除数据。
5. 低于来源 `minimum_relevance` 的 4 条重建行标记为 `filtered`，保留 `filtered` 事件，不进入公开在招列表。

生产环境升级时，应先复制 SQLite 文件并停止 worker，再初始化代码版本；不要在数据库正在写入时覆盖运行库。无法重新访问官方原文的历史岗位不能自动“补证据”，应进入人工复核队列。

## Student publication gate correction

1. 升级前备份 `runtime/job_hub.sqlite3` 至 `runtime/job_hub.pre-student-publication-gate-20260924.sqlite3`。
2. 新增 `jobs.publication_status` 与 `jobs.publication_basis_json`，属于可加性 SQLite 迁移，不删除原始岗位、证据或事件。
3. 执行 `python -m job_hub.cli reindex-jobs`，让每条历史岗位按岗位级专业与学历字段重新判定学生端发布资格。
4. 旧日报快照保持原始存档；显示时会剔除后来被判定为非本院专业的岗位，避免历史错误继续公开。

## Student experience condition correction

1. 小批量复扫 CUPB 官方就业网并接入已验证的官方来源后，运行库从 174 条增至 198 条；所有新增记录均经过同一发布门禁。
2. 升级前备份 `runtime/job_hub.sqlite3` 至 `runtime/job_hub.pre-experience-exception-gate-20260924.sqlite3`。
3. 发布判断补充“多年既往工作经验”识别；岗位写明 2 年、5 年等经验时不再因专业、学历相符而自动发布。
4. 同一岗位级条件明确写有“应届毕业生可投递/可报名”等例外时，可保留为学生端候选，仍需通过专业、学历和原文链接检查。

## 本轮复审与扩源

1. 升级前备份 `runtime/job_hub.sqlite3` 至 `runtime/job_hub.pre-publication-gate-rerun-20260924.sqlite3` 与 `runtime/job_hub.pre-source-expansion-20260924.sqlite3`。
2. 重新扫描 CUPB 职位表时按独立岗位行拆分；同一公告正文中的其他专业不得作为当前岗位的匹配证据。
3. 对 MokaHR 和自然资源部公开招聘接口执行受控分页；仅将岗位级专业、学历和官方原文同时存在的记录发布到学生端。
4. 新增 `official_snapshot_rows` 适配器和 `data/verified/pipechina-2027-geoscience-20260924.json`，保存国家管网官方招聘系统地质关键词 24 条岗位行及同一官方公告证据；动态系统仍未被绕过。
5. 新增 `data/verified/cupb-geoscience-20260924.json`，保存中国石油大学（北京）就业网公开公告核验的 4 条岗位，并在 `data/sources.json` 注册允许的官方域名白名单。
6. 运行库先备份为 `runtime/job_hub.pre-pipechina-snapshot-20260924.sqlite3`，错误的默认 `official-manual-import` 临时记录已清理；管网快照以 `--source-id pipechina-career` 导入，CUPB 快照以 `--source-id cupb-verified-geoscience-snapshot` 导入。
7. 导入后审计检查 226 条原始记录；国家管网 24 条中 21 条、CUPB 新快照 4 条通过学生端门禁，其余记录继续隔离；`audit` 为 `ok: true`。
8. 日报使用 `python -m job_hub.cli publish --refresh` 重建，当前日报统计为新增 29、在招总数 30。完整测试通过 208 项。

来源集中度当前约 70%，因此本轮只证明“真实国内岗位可以在严格门禁下增加”，不能宣称已完成全国就业信息覆盖。后续仍需逐来源接入中国石油、中国石化、中国海油下属单位和事业编/公务员职位表。
