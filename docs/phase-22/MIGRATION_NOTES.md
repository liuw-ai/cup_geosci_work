# Phase 22 迁移说明

- 数据库无新增表，无破坏性迁移。
- `data/sources.json` 新增停用来源 `pipechina-browser-capture`；初始化后来源总数从 71 增至 72。
- `data/organization_registry.json` 修正国家管网旧的“来源停用”说明，明确快照与动态捕获的边界；候选动态来源单独登记在 `data/sources.json`，避免矩阵重复计数。
- 动态捕获文件放在 `APP_DATA_DIR/captures/pipechina-career.json`，不提交 Git。文件由浏览器任务原子写入，成功后才会被采集器读取。
- 已有 `pipechina-career` 官方快照、岗位和证据不删除，动态任务失败时仍可回退到快照。
