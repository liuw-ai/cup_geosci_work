# Phase 6 交付说明

## 已交付

- [x] 独立分支：`phase/6-national-energy-source-matrix`
- [x] 四大能源体系正式入口采集准入矩阵
- [x] 21 个优先组织、37 个正式频道逐频道展开
- [x] 公开页面、动态门户、官方附件、人工导入和不可用状态契约
- [x] 访问受限与扫描成功无匹配的硬性区分
- [x] 管理员 API 和 CLI
- [x] 无数据库迁移、可直接回退
- [x] 真实入口只读健康检查记录
- [x] 完整测试、静态检查、Compose 配置和桌面/移动端复核
- [ ] 用户审阅通过后合并 `master` 并创建稳定版本标签

## 审阅重点

1. 矩阵是否覆盖当前优先的三桶油、国家管网、研究院、油田和集团内技术服务单位。
2. `access_limited`、`accessible_structure_unverified` 和 `scan_success_no_match` 的边界是否清楚。
3. 当前没有公开 API 结论是否符合证据要求。
4. 下一批应优先补哪一组单位或哪一种正式公告/职位表入口。

## 建议审阅命令

```powershell
python -m pytest -q tests/test_national_sources.py
python -m job_hub.cli national-source-matrix
python -m job_hub.cli national-source-matrix --runtime-status access_limited
```
