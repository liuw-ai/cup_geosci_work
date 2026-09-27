# Migration Notes

## 数据与来源配置

`sinopec-career` 的快照路径更新为 `data/verified/sinopec-geoscience-20260928.json`，并开启 `reconcile_missing_external_ids`。不需要数据库结构迁移。

同步前已保留服务器数据库备份。同步事务会更新当前 348 条岗位，并将完整官方清单中缺失的旧外部 ID 标记为 `withdrawn`，原岗位和证据仍保留在数据库历史中。

## 运行时挂载

`docker-compose.yml` 和 `docker-compose.browser.yml` 将版本化 `data/` 目录以只读方式挂入应用和浏览器 worker，容器重建后仍能读取最新快照；运行数据继续写入 `/var/lib/job-hub`。
