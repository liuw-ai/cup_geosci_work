# Phase 4 交付说明

## 已交付

- 新增省级来源核验契约和 `source_validation.py`。
- 新增 9 条真实官方来源核验台账记录。
- 新增 6 个最小离线 HTML 夹具，覆盖北京、天津、安徽、山东、河南和山东人社候选源。
- 为官方公告适配器补充河南“招聘联考”标题模式，避免真实公告被静默漏掉。
- 新增受 `X-Admin-Token` 保护的 `/api/admin/source-validation-matrix`，支持省份、角色和核验阶段筛选。
- 新增 `source-validation-matrix` CLI，输出私有证据和运行状态。
- 公开覆盖报告只保留省级核验聚合指标，不泄露内部样例和备用入口。
- 增加数据契约、目标矩阵、解析回归和学生端隐私边界测试。
- 更新 `README.md`、`ARCHITECTURE.md`，明确 Phase 4 的状态语义和未完成范围。

## 审阅清单

- [x] 独立分支：`phase/4-provincial-official-source-validation`
- [x] 真实官方样例 URL 与字段证据已登记
- [x] 六个离线夹具不触发网络访问
- [x] 解析器保留岗位原公告链接，排除流程性通知
- [x] candidate 来源不因夹具通过而启用
- [x] 管理员接口鉴权与公开聚合隔离
- [x] 无 SQLite 迁移说明
- [x] 无新增公开岗位、无虚构数据
- [x] 完整测试、编译、JSON 和空白检查
- [ ] Docker Compose 最终静态校验和浏览器截图待交付前完成
- [ ] 用户审阅通过后才合并 `master`、创建 `v0.8.0`

## 建议审阅顺序

1. 阅读 `QUALITY_REPORT.md`，确认指标不是岗位总数。
2. 打开 `data/source_validation_registry.json`，逐条检查官方 URL、样例标题、截止日期和附件证据。
3. 执行 `python -m pytest -q tests/test_source_validation.py`，确认六个夹具回归。
4. 启动本地站点，查看公开 `/api/coverage` 不含内部 URL，再用管理员令牌查看核验矩阵。
5. 只在确认阶段边界和字段证据后，决定下一批省份与来源角色。
