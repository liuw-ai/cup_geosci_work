# Phase 77 质量报告

## 本地验证

```text
python -m pytest -q: 372 passed
python -m compileall -q job_hub: passed
docker compose config --quiet: passed
docker compose -f docker-compose.browser.yml config --quiet: passed
git diff --check: passed
```

## 服务器部署前基线

阶段 76 工作区已在服务器独立目录 `/home/ubuntu/cup_geosci_work_phase76` 建立，旧工作区保留。切换前数据库已备份到 `/opt/backups/job_hub-before-phase76-*.sqlite3`。

阶段 76 代码在服务器运行时的基线：

- 数据库岗位总数：1477；
- 当前审计开放岗位：206；
- 中石化单位总数：132；
- 地质候选单位：35；
- 候选单位详情完成：35/35；
- 中石化岗位行：363；
- 审计问题：0；
- Worker 心跳：通过；
- SLB 详情预算故障：1，待阶段 77 配置部署后复核；
- 公网正式域名：仍需单独核验，不能因 IP 服务正常而宣称公网正式版完成。

## 解释

“审计问题为 0”只表示已发布数据满足结构和证据门禁，不代表所有来源均可访问。来源失败必须保留在运行报告和人工复核队列中。
