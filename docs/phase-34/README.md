# Phase 34：中国煤炭地质总局招聘栏目迁移

本阶段修复一个真实的来源路径变更：旧入口 `/rlzy/rczp.htm` 已返回 404，官方首页的人力资源栏目现指向 `/?mod=zhaopin&tid=129`。

## 交付内容

- 通过中国煤炭地质总局官方首页核验新的人才招聘栏目和 17 页分页结构。
- 将 `ccgc-careers` 的列表入口、详情选择器和详情 URL 规则切换到新栏目。
- 保留最近公告的招聘正文、官方详情链接和现有地学相关度/开放期门禁。
- 访问失败与扫描成功无匹配继续分开记录；旧入口 404 不再作为启用来源持续失败。

## 边界

新栏目中的公告索引数量不等于地学岗位数量。每个详情页仍须通过招聘内容、专业相关度和开放期检查；已关闭公告不会因栏目迁移而重新发布。

## 检查

```bash
python -m pytest -q
python -m job_hub.cli audit
docker compose exec worker python -m job_hub.cli sync-source ccgc-careers
```
