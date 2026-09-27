# Migration Notes

## 配置

新增环境变量：

```text
GOVERNMENT_POSITION_MAX_AGE_HOURS=48
```

它只作用于 `data/government_position_registry.json` 的逐岗位发布，不会绕过官方截止日期。服务器未配置时使用代码默认值 48 小时。

## 同步行为

`DailyWorker._sync_verified_government_positions()` 现在：

1. 读取官方职位表台账并检查 `as_of` 新鲜度；
2. 只发布当前日期仍有效、专业匹配状态为 `explicit_match` 或 `unrestricted_match` 的行；
3. 对已注册来源调用稳定外部编号清退；
4. 台账过期时以空的当前集合撤回旧政府岗位；
5. 来源没有注册时只计入 `skipped` 并记录错误，不执行危险清退。

## 运维命令

```bash
python -m job_hub.cli government-position-audit \
  --path data/government_position_registry.json \
  --today 2026-09-28 \
  --max-age-hours 48
```

服务器更新代码和版本化数据后，重启 `worker`，再检查：

```bash
docker compose ps
docker compose exec -T worker python -m job_hub.cli worker-health --max-age 300
docker compose exec -T worker python -m job_hub.cli government-position-audit --today 2026-09-28 --max-age-hours 48
docker compose exec -T web python -m job_hub.cli audit
```
