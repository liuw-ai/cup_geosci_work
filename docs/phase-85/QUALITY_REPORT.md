# Phase 85 质量报告

## 结论

通过代码级验收，待服务器真实运行验证。此阶段提高的是动态官方来源的连续采集能力，未新增或伪造任何岗位。

## 已验证门禁

| 检查项 | 结果 |
| --- | --- |
| 仅接受 recent + pagination-complete partial | 已测试 |
| 失败详情必须有官方 allowlist URL | 已测试 |
| 所有失败详情补齐才形成 success | 已测试 |
| 任一详情仍失败 | 形成新的 partial 归档，不覆盖 success-only 文件 |
| worker 优先定向重试 | 已测试，不触发全量列表扫描 |
| 归档过期 | 已测试，才允许回退到全量扫描 |
| 历史 partial 隔离 | 已测试，先归档后移动，不删除 |
| 参数归属 | 已测试，仅国聘来源获得重试配置 |
| 完整测试集 | `399 passed, 1 skipped` |
| Python 编译 | `python -m compileall -q job_hub` 通过 |
| Compose 配置 | 默认与 browser Compose 配置均通过 |
| JSON 与 Git 空白检查 | `python -m json.tool data/sources.json`、`git diff --check` 通过 |

## 服务器验收输入

服务器首次运行后需要记录：失败归档路径、17 个详情的重试成功数/失败数、是否生成 `success`、字段完整率、学生画像明确匹配数、worker 心跳和连续两轮运行结果。未获得这些真实结果前，国聘动态来源保持停用，仍由已审阅快照服务学生端。
