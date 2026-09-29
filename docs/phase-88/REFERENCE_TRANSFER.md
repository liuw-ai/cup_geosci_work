# 参考资料到本项目的迁移记录

本阶段只迁移与“可维护、可审计、合规运行”直接相关的方法，没有把第三方项目当作官方岗位来源。

| 资料/项目 | 已核实的方法 | 本项目的落点 | 明确不采用 |
|---|---|---|---|
| `参考工具/wehire-monitor-main` | 状态机、失败状态、人工复核、断点续跑、运行日报 | `run_ledger.py` 的来源结论和日报计数 | 不复制其业务字段或第三方发现内容 |
| `参考工具/JustHireMe-main` | source adapter、live smoke、发布前 checklist、回退 | `source-run-ledger` 固定验收命令和阶段报告 | 不把 smoke 通过当成岗位真实性证明 |
| `参考工具/Scrapling-main` | adaptive parsing、缓存回放、限速、暂停/恢复 | 保留现有响应缓存，并记录请求 telemetry | 不使用其反爬绕过能力 |
| `参考工具/curl_cffi-main` | 浏览器兼容 TLS 的可选传输 | 保留 `HTTP_CLIENT=curl_cffi` 选项并记录 transport mode | 不把 TLS 指纹当作绕过 403/robots 的手段 |
| RFC 9309 | robots 是访问策略；5xx/不可达不能假定可抓取 | `access_limited`/`source_unavailable`，不发布为无岗位 | 不将 robots 视为授权或绕过对象 |
| Playwright Python 网络文档 | 监听 request/response，捕获和发布分离 | 浏览器捕获仍经过完整性、证据和专业门禁 | 不因一次渲染成功就发布整批岗位 |
| Scrapy AutoThrottle 文档 | 按响应延迟、Retry-After、状态码调整请求 | 本阶段先记录重试；下一阶段再做独立 per-host 自适应限速 | 不在本阶段散落修改每个 adapter 的 sleep |

官方资料链接：

- https://www.rfc-editor.org/rfc/rfc9309.txt
- https://playwright.dev/python/docs/network
- https://docs.scrapy.org/en/latest/topics/autothrottle.html

