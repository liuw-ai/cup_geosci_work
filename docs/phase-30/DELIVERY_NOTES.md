# Phase 30 交付说明

- [x] 新分支：`phase/30-public-deployment`
- [x] Compose 公网绑定可配置，默认仍为 localhost
- [x] `.env.example` 和部署文档更新
- [x] 数据库与学生端发布门禁保持不变
- [x] 全量测试与 Python 编译检查
- [x] 服务器配置公网绑定
- [x] 云安全组放行 HTTP/80
- [x] 外部网络 HTTP、首页、岗位详情和移动端 User-Agent 验证
- [ ] 正式域名与 HTTPS 反向代理

审阅通过并完成外部访问验证后创建：

```bash
git tag -a phase-30-review -m "Phase 30 public deployment baseline review"
git push origin phase/30-public-deployment phase-30-review
```
