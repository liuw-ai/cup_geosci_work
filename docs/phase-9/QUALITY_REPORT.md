# Phase 9 质量报告

日期：2026-09-22
分支：`phase/9-transport-acquisition-ladder`
基线：`phase-8-review`（`6639f86`）

## 当前产品基线

| 指标 | 当前值 | 解释 |
| --- | ---: | --- |
| 学生端公开岗位 | 52 | 真实岗位库当前数量，不代表全国覆盖完成 |
| 已登记来源 | 64 | 注册表数量，不等于全部启用或可访问 |
| `source_active` | 33 | 最近健康状态为可访问的来源 |
| `source_degraded` | 26 | 栏目/网络/解析需处理，不能判定无岗位 |
| `source_blocked` | 3 | robots 或访问策略限制 |
| 官方原文链接完整率 | 100% | 已发布岗位均有官方链接 |
| 专业标签完整率 | 100% | 已发布岗位均有匹配标签 |
| 地点完整率 | 88.46% | 海外及部分公告地点仍需完善 |
| 省份字段完整率 | 5.77% | 当前国内岗位省份标准化仍是主要缺口 |
| 明确截止日期完整率 | 7.69% | 很多官方公告未给统一固定截止日 |
| 岗位集中 | Halliburton 28、SLB 14 | 国内来源数量不足的直接表现 |

质量门禁当前为 `needs_attention`。本阶段不隐藏这些缺口，也不以入口登记数替代岗位覆盖。

## 真实入口直连探测

运行：`2026-09-22`，环境：`local-direct-diagnostic`，模式：`direct`，代理变量存在：是；共 4 个体系、8 次入口尝试。

| 分类 | 数量 |
| --- | ---: |
| `access_policy_block` | 4 |
| `accessible_html` | 2 |
| `accessible_dynamic_shell` | 1 |
| `source_unavailable` | 1 |

体系结论：2 个 `access_limited`（中国石油、中国石化），2 个 `accessible_structure_unverified`（中国海油、国家管网）。国家管网官网主页发现了正式招聘入口 `https://zhaopin.pipechina.com.cn/recruit`，但该招聘子域的 robots 返回 403；中国海油官网发现公告栏目 `https://www.cnooc.com.cn/zxzx/ggxx/`。这些均未产生岗位。

## 自动化验证

提交前执行以下检查并记录实际结果：

```text
python -m pytest -q                         -> 144 passed in 5.57s
python -m pytest -q tests/test_transport.py tests/test_entry_probes.py tests/test_source_health.py tests/test_sources.py tests/test_attachments.py -> 41 passed
python -m compileall -q job_hub tests       -> passed
python -m json.tool data/national_entry_targets.json -> passed
python -m json.tool data/national_entry_probe_runs.json -> passed
python -m json.tool data/national_entry_probe_runs_direct_diagnostic.json -> passed
git diff --check                            -> passed
docker compose config --quiet               -> passed（使用临时 `.env.example` 占位文件，随后已删除）
```

离线测试不依赖目标站点偶然可用；直连 JSON 是只读探测的原始证据，契约测试会阻止缺字段或来源错配的记录进入审阅包。

## 页面复核

本阶段没有学生端前端源代码变更，复用 Phase 8 基线截图确认没有视觉回归：

| 视口 | 文件 | 结果 |
| --- | --- | --- |
| 桌面 | `screenshots/home-desktop-1280.png` | 页面正常渲染 |
| 手机 | `screenshots/home-mobile-390.png` | 无横向溢出 |
| 手机菜单 | `screenshots/home-mobile-menu-open-390.png` | 菜单展开可见 |

## 未解决问题

1. 直连只解决了“本机代理是否参与”的诊断问题，不能突破中国石油/中国石化/国家管网的访问策略。
2. 四大能源体系仍没有达到可发布岗位的字段适配和证据门禁，国内岗位总量不会因本阶段自动增加。
3. 要扩大数量，必须在服务器上复测并逐个接入下属油田、研究院、工程技术单位、官方公告和职位表；不能重复请求被限制的统一入口。
4. 省份、截止日期和附件字段完整率仍是下一阶段验收重点。
