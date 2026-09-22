# Phase 10 质量报告

日期：2026-09-22
分支：`phase/10-official-energy-announcements`
基线：`phase-9-review`（`50edebd`）

## 本阶段真实结果

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| 学生端公开在招岗位 | 53 | 基线 52；本阶段真实新增 BGP 1 条 |
| BGP 直连健康状态 | `source_active` | 招聘页 HTTP 200，robots 404 |
| BGP 同步 | 1 发现 / 1 在招 / 1 新增 | 通过现有岗位质量门禁 |
| 官方证据 URL 完整率 | 100% | 53/53 |
| 地点完整率 | 88.68% | 47/53，BGP 地点完整 |
| 学历标签完整率 | 81.13% | 43/53 |
| 专业标签完整率 | 100% | 53/53 |
| 截止日期或来源策略完整率 | 98.11% | 52/53；BGP 使用 120 天无截止日期策略 |
| 来源集中度最高来源 | Halliburton 52.83% | 国内岗位覆盖仍明显不足 |

BGP 本身的字段验证为：标题、单位、学历、专业、地点和发布日期均有官方原文证据；页面未给固定截止日期和独立报名 URL，报告明确保留为空。

本机公开 API 查询 `GET /api/jobs?query=Seismic%20Data%20Processing%20Geophysicist` 返回 HTTP 200 和 53 条在招结果总数，其中 BGP 记录显示分类为“油气工程技术服务”，官方原文 URL 与证据 URL 均为 BGP 页面。

## 访问边界

- 中国石油统一招聘、中国石化、国家管网的访问受限记录没有被改写为“扫描成功无匹配”。
- 中国海油动态入口仍为结构待适配/公开接口业务错误，不发布岗位。
- 本阶段没有使用 TLS 指纹伪装、代理轮换、验证码服务、登录或绕过 robots/WAF 的方式。
- 服务器直连复测尚未执行：当前工作区没有服务器 SSH、部署地址或控制台权限。报告只提供可复制命令，不能把本机结果冒充服务器结果。

## 自动化验证

提交前执行：

```text
python -m pytest -q                                      -> 147 passed
python -m compileall -q job_hub tests                    -> passed
python -m json.tool data/sources.json                    -> passed
python -m json.tool data/organization_registry.json      -> passed
python -m json.tool data/national_source_matrix.json     -> passed
git diff --check                                         -> passed
```

新增回归覆盖：

- BGP 官方页面字段解析和证据 URL 保留；
- 多标题与内容块数量不一致时拒绝采集；
- `structured_opening_page` 来源配置缺少字段选择器时契约失败；
- 国家能源矩阵识别 BGP 的 `verified_public` / `scan_success_with_open_matches` 状态。

## 页面检查

本阶段没有改变学生端模板或 CSS。以下截图从 Phase 9 复用，作为视觉基线，不宣称本阶段有新的前端功能：

- [桌面首页](screenshots/home-desktop-1280.png)：布局正常。
- [手机首页](screenshots/home-mobile-390.png)：无横向溢出。
- [手机菜单](screenshots/home-mobile-menu-open-390.png)：菜单展开状态可见。

## 未解决问题

本阶段只验证并接入了一个中国石油下属单位公开页，且该条为海外岗位。它证明了“下属单位官网公告 -> 结构化字段 -> 官方证据 -> 学生端岗位”的链路可运行，但没有完成中国石油、中国石化、中国海油、国家管网下属单位矩阵，也没有消除国内岗位来源集中问题。下一阶段应继续逐站验证国内油田、研究院和工程技术单位，并在服务器执行 `direct` 复测。
