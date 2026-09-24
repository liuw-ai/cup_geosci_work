# Phase 26 Quality Report

日期：2026-09-25  
分支：`phase/26-server-source-diagnostics`

## 验证结果

```text
python -m pytest -q tests/test_source_health.py tests/test_pipeline.py tests/test_entry_probes.py
27 passed

python -m pytest -q
238 passed
```

Docker 配置和既有数据库审计未修改。当前尚未连接用户未来的云服务器，因此本报告不宣称已经完成服务器直连；上线后必须提交 `source-health-server.json`，再逐来源决定是否启用动态适配器。

## 已知边界

- HTTP 412、403、robots 禁止和 TLS EOF 仍然是访问状态，不会被伪装成成功；
- `source_active` 只证明入口可访问，不证明存在地学岗位；
- 诊断命令不会采集或发布岗位；
- curl-cffi 只用于合规的浏览器 TLS/HTTP2 兼容，不绕过访问控制；
- 中石化 132 个单位、35 个候选单位详情采集仍待服务器浏览器/API 适配阶段完成。
