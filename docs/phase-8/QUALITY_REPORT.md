# Phase 8 质量报告

日期：2026-09-22
分支：`phase/8-national-energy-public-probes`
基线：`phase-7-review`（`f7de216`）

## 覆盖与真实性指标

| 指标 | 结果 | 解释 |
| --- | ---: | --- |
| 登记体系 | 4 | 中国石油、中国石化、中国海油、国家管网 |
| 每体系登记入口 | 2 | 一个主招聘入口 + 一个备用/官网入口 |
| 入口尝试 | 8 | 4 个体系全部实际执行了只读探测 |
| `source_unavailable` | 6 | TLS EOF，不能解释为无岗位 |
| `accessible_dynamic_shell` | 1 | 中国海油招聘页可达但没有稳定公开链接 |
| `accessible_html` | 1 | 中国海油集团官网可达 |
| 发现官方招聘/公告链接 | 1 | CNOOC 官网公告栏目，尚未完成字段适配 |
| 新增学生端岗位 | 0 | 本阶段不产生岗位，且没有把失败响应当作空结果 |
| 可发布来源新增 | 0 | 四个体系仍需专用适配器和原文审计 |

备用入口登记率为 100%，但“登记”不等于“可访问”；本次 8 次尝试中只有中国海油的两个入口完成 HTTP 层读取。当前没有任何体系达到“扫描成功且字段完整、可发布”的状态，因此没有生成“扫描成功无匹配”结论。

## 字段与证据检查

入口探测记录对每次尝试保留入口 URL、robots URL、robots 状态、HTTP 状态、最终 URL、Content-Type、分类、错误类别、错误详情、发现链接和运行重试次数。探测契约会拒绝缺少这些字段或引用未登记官方主机的记录。

岗位字段完整率、专业匹配率和附件解析成功率在本阶段不适用，因为没有岗位采集结果。它们必须在下一阶段的专用适配器中按岗位逐条计算，不能用入口可达率代替。

## 自动化验证

提交前执行结果：

```text
python -m pytest -q                         -> 138 passed in 5.32s
python -m pytest -q tests/test_entry_probes.py -> 10 passed
python -m compileall -q job_hub tests       -> passed
python -m json.tool data/national_entry_targets.json -> passed
python -m json.tool data/national_entry_probe_runs.json -> passed
git diff --check                            -> passed
docker compose config --quiet               -> passed
```

测试使用离线夹具，不把网络偶然性伪装成稳定成功；真实探测原始记录保存在 `data/national_entry_probe_runs.json`。

## 页面复核证据

本阶段没有学生端前端代码变更，以下截图从 Phase 7 原样复用，用于证明入口探测改动没有改变既有桌面/手机布局：

| 视口 | 文件 | 复核结果 |
| --- | --- | --- |
| 桌面 | `screenshots/home-desktop-1280.png` | 页面正常渲染 |
| 手机 | `screenshots/home-mobile-390.png` | 无横向溢出 |
| 手机菜单 | `screenshots/home-mobile-menu-open-390.png` | 菜单展开状态可见 |

截图不是本阶段新增视觉功能的证明；它们只记录基线未回归。

## 未解决风险

1. 本地网络无法完成中国石油、中国石化和国家管网的 TLS 验证，需要服务器网络重新执行。
2. 中国海油动态页面和官网公告栏目仍未完成岗位列表、详情、附件字段核验。
3. 入口探测器不具备浏览器渲染能力，不能把动态壳当作岗位数据。
4. 当前四个来源均停用，国内岗位数量不会因本阶段增加；下一阶段必须以真实官方原文和字段完整率验收。
