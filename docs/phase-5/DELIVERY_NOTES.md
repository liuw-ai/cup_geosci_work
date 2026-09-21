# Phase 5 交付说明

## 已交付

- [x] 独立分支：`phase/5-discovery-verification`
- [x] 私有发现源注册表和强制 `private_discovery_only` 契约
- [x] 9 个真实公开入口登记及当前网络访问状态记录
- [x] 线索规范化 URL 指纹、重复合并和多渠道 mention 归因
- [x] 官方来源域名匹配、不匹配和人工批准状态
- [x] 管理员来源台账、线索漏斗和 mention 接口
- [x] CLI 查看命令和按来源/省份筛选
- [x] 旧 SQLite 数据库加法迁移与回归测试
- [x] 114 项自动化测试、编译和空白检查
- [x] 学生端公开接口仍不返回第三方线索
- [ ] 用户审阅通过后才合并 `master` 并创建正式稳定标签

## 审阅重点

1. `data/discovery_sources.json` 中的渠道是否符合“只作发现线索”的范围。
2. `manual_review_approved` 是否要求管理员实际核对官方主体、正文和报名入口。
3. 多渠道重复线索的合并和 mention 归因是否满足后续大规模导入。
4. 迁移说明是否足以支持服务器旧库备份和回退。
5. 是否保持“第三方发现数量不等于正式岗位数量”的统计边界。

## 建议审阅命令

```powershell
python -m pytest -q tests/test_discovery.py tests/test_leads.py
python -m job_hub.cli discovery-sources
python -m job_hub.cli discovery-funnel
```
