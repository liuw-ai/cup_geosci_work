# Phase 8 迁移说明

## 数据库

本阶段没有 SQLite schema 迁移。新增的入口目标和最近一次探测运行是版本化 JSON：

- `data/national_entry_targets.json`
- `data/national_entry_probe_runs.json`

已有岗位、来源健康、抓取运行、日报和附件表结构不变。部署前不需要执行数据库升级；生产回退仍应先备份 `APP_DATABASE_PATH` 和 `APP_DATA_DIR`。

## 配置与权限

- 没有新增环境变量、密钥或第三方账号。
- 四个国家能源来源继续保持 `enabled: false`，入口探测不会自动启用岗位采集器。
- 管理员接口复用现有 `X-Admin-Token`，不向公开 API 或学生页面返回探测详情。
- 探测结果中的错误信息是运行审计数据，不能作为岗位正文或学生端提示。

## 运行方式

```powershell
python -m job_hub.cli national-entry-probe --environment production-server --output .\runtime\national-entry-probe.json
```

运行结果应由管理员审阅后复制到版本化证据文件；网络故障时不要手工改写为 `accessible` 或“无岗位”。若需要在服务器定期执行，应由运维任务调用 CLI，并保留运行日期、环境标签和原始错误。

## 回退

回退到 `phase-7-review` 不需要数据库降级：

```powershell
git switch phase/7-cnooc-public-adapter
git restore --source phase-7-review -- data/national_entry_targets.json data/national_entry_probe_runs.json
python -m pytest -q
```

更稳妥的生产回退方式是切换到已审核标签，并从备份恢复运行目录；不要删除 SQLite 文件来“清理”本阶段数据。
