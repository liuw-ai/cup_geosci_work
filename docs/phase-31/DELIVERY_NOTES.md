# Phase 31 交付说明

- [x] 独立分支：`phase/31-sinopec-live-capture`
- [x] 132 个官方单位清单保留
- [x] 35 个候选单位逐一打开官方详情页
- [x] 35/35 候选单位分页完成
- [x] 397 条岗位行和岗位级字段证据
- [x] 专业、学历、地点、人数、截止日期门禁
- [x] 截止时间标准化漏洞修复
- [x] 246 项全量测试和数据库审计
- [x] 服务器部署和 Worker 同步验证
- [ ] 服务器无人值守 Chromium 每日自动捕获
- [ ] 中国石油、中国海油、国家管网专用适配器
- [ ] 事业编和公务员官方职位表扩源

审阅通过后创建：

```bash
git tag -a phase-31-review -m "Phase 31 Sinopec official detail capture review"
git push origin phase/31-sinopec-live-capture phase-31-review
```
