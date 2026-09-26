# Migration Notes

1. `data/government_position_registry.json` 增加中国地震局2027年度事业单位官方附件的70条地学匹配岗位（89个计划名额），并为每条职位增加 `deadline_policy`、单位继承后的原始Excel行号和工作地点。
2. `data/government_artifact_manifest.json` 增加湖北官方 XLSX 和中国地震局官方 XLSX 附件，并为所有附件增加 `deadline_policy`。
3. `job_hub/government_positions.py` 和 `job_hub/government_artifacts.py` 增加固定截止/招满即止校验；没有数据库表结构迁移。
4. worker 继续使用 `deadline_date` 清退固定截止岗位；招满即止岗位不会因空截止日自动过期，需来源复核或人工关闭。
5. 服务器部署时重新运行政府附件登记命令和 worker 同步，已存在岗位按官方来源、单位、岗位代码和证据 URL 幂等更新。
