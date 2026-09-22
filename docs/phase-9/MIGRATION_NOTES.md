# Phase 9 迁移说明

## 数据库

本阶段没有 SQLite schema 迁移。岗位、来源健康、抓取运行、附件和日报表结构保持不变。新增或更新的入口运行证据是版本化 JSON：

- `data/national_entry_targets.json`
- `data/national_entry_probe_runs.json`
- `data/national_entry_probe_runs_direct_diagnostic.json`

生产部署前仍应备份 `APP_DATABASE_PATH` 和 `APP_DATA_DIR`，但不需要执行数据库升级脚本。

## 配置

新增环境变量：

```dotenv
HTTP_TRANSPORT_MODE=environment
```

可选值只有 `environment` 和 `direct`。默认值保持 `environment`，因此旧部署行为不变。服务器必须先确认网络出口、证书链和组织网络政策，再切换为 `direct`；不能为了修复错误关闭 TLS 校验。

入口探测也支持命令行覆盖环境变量：

```bash
python -m job_hub.cli national-entry-probe --transport direct
```

命令行参数只影响本次探测，不会修改 `.env`。

## 证据兼容

旧的入口运行 JSON 没有传输字段时，契约将其规范化为 `transport_mode: "unknown"`，因此历史证据仍可读取，但不能用来比较代理与直连结果。新运行必须写入：

- `transport_mode`
- `proxy_environment_present`

## 回退

回退到 Phase 8 不需要数据库降级：

```powershell
git switch phase/8-national-energy-public-probes
```

若只回退配置或证据文件，使用已审核提交中的对应文件恢复；不要删除 SQLite 文件。生产回退前先停止 worker、备份运行目录，再切换到目标标签并运行 `audit` 与 `worker-health`。
