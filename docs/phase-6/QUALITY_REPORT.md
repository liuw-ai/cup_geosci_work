# Phase 6 质量报告

日期：2026-09-22
分支：`phase/6-national-energy-source-matrix`

## 覆盖与真实性

| 指标 | 结果 |
| --- | ---: |
| 目标体系 | 4 |
| 目标组织 | 21 |
| 正式频道展开行 | 37 |
| 频道备份入口率 | 100% |
| 统一入口独立评估 | 4 |
| 公开 API 已验证 | 0 |
| 访问受限频道 | 13 |
| 动态结构待适配频道 | 3 |
| 扫描成功无匹配 | 0 |
| 新增公开岗位 | 0 |

“访问受限频道”和“扫描成功无匹配”由契约分开，测试会拒绝将前者标记成后者。

## 只读入口检查

2026-09-22 使用项目标识 User-Agent 对四个统一招聘入口各发起一次低频只读请求：

| 来源 | 结果 | 结论 |
| --- | --- | --- |
| 中国石油高校毕业生招聘平台 | TLS EOF | `source_unavailable`，不是无岗位 |
| 中国石化人才招聘网 | TLS EOF | `source_unavailable`，不是无岗位 |
| 中国海油招聘入口 | HTTP 200；HTML 无岗位链接 | `structure_needs_adapter` |
| 国家管网招聘平台 | TLS EOF；历史记录含 robots 403 | `source_unavailable`，不是无岗位 |

没有尝试绕过限制、登录、验证码、代理或浏览器指纹校验。

## 自动化检查

```text
python -m pytest -q                                      119 passed
python -m json.tool data/national_source_matrix.json     passed
python -m json.tool data/source_targets.json             passed
python -m compileall -q job_hub tests                    passed
git diff --check                                         passed
docker compose config --quiet                            passed
```

浏览器复核使用临时数据库和本地预览服务完成：桌面截图为 1280x900，手机截图为 375x812；手机页面 `clientWidth` 与 `scrollWidth` 均为 375，折叠菜单的 `aria-expanded` 能从 `false` 切换为 `true`。预览服务已在检查结束后停止，未写入正式运行数据库。

## 剩余风险

1. 21 个组织是优先种子，不是三桶油全部下属单位；仍需分批扩充油田、研究院和技术服务单位。
2. 统一招聘入口尚未形成可公开、稳定、可回溯的岗位字段适配器。
3. 中海油页面虽然可访问，但动态脚本接口是否公开、允许自动化以及字段是否完整仍未验证。
4. 服务器网络环境可能与本地不同，所有状态需在部署环境重新健康检查。
