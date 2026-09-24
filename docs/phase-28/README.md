# Phase 28：中石化专用单位扫描契约

本阶段把中国石化校园招聘 SPA 的“单位清单、候选单位、详情岗位和分页状态”拆成可审计的独立契约。它是服务器 Chromium/API 采集器的前置层，不把历史快照伪装成实时成功。

## 本阶段交付

- `job_hub/sinopec_scan.py`：生成 132 个单位的有序详情扫描计划，标记 35 个候选单位，并验证岗位行绑定到已知 `deptId`。
- `job_hub/sinopec.py`：归一化单位级分页、发现行数、导出行数和失败行指标；解析 Vue hash 路由中的 `deptId`。
- `job_hub/cli.py`：`sinopec-capture` 增加 `--require-scan-complete` 严格门禁和 `--plan-output` 计划导出。
- `tests/test_sinopec_scan.py`：覆盖 132/35 完整性、未知单位、分页未完成和严格门禁。

## 使用

```bash
python -m job_hub.cli sinopec-capture \
  --path data/verified/sinopec-geoscience-20260924.json \
  --plan-output runtime/sinopec-scan-plan.json
```

服务器浏览器 worker 应按计划逐个打开 `detail_url`，在每个单位写入 `scan_metrics`：

```json
{
  "pages_scanned": 2,
  "pages_expected": 2,
  "jobs_discovered": 15,
  "jobs_exported": 15,
  "failed_jobs": 0,
  "pagination_complete": true
}
```

生产提升前执行：

```bash
python -m job_hub.cli sinopec-capture \
  --path runtime/sinopec-capture.json \
  --require-complete \
  --require-scan-complete
```

任何候选单位仍为 `listed`、分页未完成、存在失败行或无法绑定官方详情的捕获都会被拒绝。`access_limited`、`parse_failed` 和 `manual_review_required` 会保留为失败诊断，不能被解释成“无岗位”。

## 参考项目落点

- Scrapling/Firecrawl 的动态页面与分页思路只用于浏览器 worker 的实现参考；本阶段没有绕过访问控制。
- JustHireMe 的来源适配器、状态机和质量门落在 `sinopec_scan.py` 的单位状态审计上。
- curl-cffi 仍属于服务器 HTTP/TLS 兼容选项，不改变 robots、验证码或登录限制。

