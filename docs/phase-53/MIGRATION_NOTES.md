# Phase 53 迁移说明

## 代码与配置

- 新增来源类型：`cmgb_browser_rows`。
- 新增来源注册：`cmgb-iguopin-browser`，默认关闭。
- 新增 Compose 服务：`cmgb-browser`，复用 `headless-shell` 和共享数据卷。
- 浏览器入口 `job_hub.cnpc_browser_entrypoint` 通过 `BROWSER_WORKER_KIND=cmgb` 选择国聘 worker；未设置时仍运行 CNPC worker。

## 首次服务器启用步骤

1. 构建包含最新代码的镜像，并确认 `headless-shell` 的 CDP 可达。
2. 先以 `BROWSER_WORKER_KIND=cmgb` 运行一次，不打开来源发布开关，检查捕获文件中的页数、详情成功数和字段完整率。
3. 将捕获文件复制到 `APP_DATA_DIR/captures/cmgb-iguopin-browser.json`，执行 `python -m job_hub.cli audit`。
4. 只有在 8 页分页完整、详情失败为 0、字段完整率为 100% 且学生门禁抽查通过后，才把 `cmgb-iguopin-browser` 的 `enabled` 改为 `true`。
5. 启用后保留 Phase 52 快照至少一个发布周期，确认指纹去重和开放岗位数量没有异常增加，再决定是否退役快照来源。

## 回退

将 `cmgb-iguopin-browser` 改回 `enabled: false` 即可停止动态来源；Phase 52 快照不会被删除。代码可回退到 `v0.22.41`。

