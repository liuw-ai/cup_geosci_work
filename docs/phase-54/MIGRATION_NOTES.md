# Migration Notes

## Code

- 国聘详情页采用 `.job-banner .title`、`.company-title`、`.address` 和 `.overview-item` 结构。
- `专业要求` 的摘要值可能是“详见职位描述”，解析器会继续读取 `.job-duty`，提取“专业要求”段落；没有明确专业证据时拒绝该详情。
- 捕获 ID 优先使用官方详情 URL 的 `id` 参数，避免分页顺序变化产生重复岗位。
- 捕获失败会记录页码、行号、卡片 ID、详情 URL 和异常原因，供人工复核。
- `official_cmgb_browser_detail` 已加入岗位级证据白名单；没有这一步，完整捕获会被错误标记为 `pending_evidence`。
- 完整浏览器捕获不受普通网络来源 `MAX_SOURCE_ITEMS`（默认 80）截断，适配器按来源 `max_items` 消费，硬上限为 2,000；否则 146 条捕获会静默只导入前 80 条。

## Server isolation

本阶段只使用 `/opt/cup_geosci_phase54` 和 `cupb-phase54-headless-shell-1`。旧生产目录 `/opt/cup_geosci_work`、生产 web/worker/caddy 容器均未改动。

## Publication rule

不要把 `partial` 或 `parse_failed` 捕获复制到生产数据目录。只有 `status=success`、分页完整、详情失败数为零且所有必需字段和官方证据通过校验，才可以由正常 source collector 消费。

## Server isolation result

提交 `23f9400` 在服务器隔离卷中复测：发现 146 条、导入 77 条、明确匹配 16 条、待核验 6 条、专业不匹配 55 条；字段完整率 77/77。隔离卷已清理，现网 `web`、`worker` 和 Caddy 未重启；代码目录保留在 `/opt/cup_geosci_phase54/app-23f9400` 供审计。
