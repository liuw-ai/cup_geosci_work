# Quality Report

## Automated checks

- `pytest -q`：184 passed
- `git diff --check`：passed
- 专业分类专项测试：5 passed
- 专业画像与通用匹配回归：23 passed
- 组织/来源/采集器回归：29 passed

## Current runtime baseline after reindex

| 指标 | 值 | 说明 |
|---|---:|---|
| 当前在招岗位 | 91 | 本地运行库，仍以官方原文为准 |
| 已登记来源 | 70 | 包含禁用、故障和待核验来源 |
| 启用来源 | 31 | 只有启用来源进入 worker |
| 最近可访问来源 | 9 | 其余来源需修复或复测 |
| 来源故障/受限 | 59 | `source_degraded` 35、`source_blocked` 24 |
| 明确专业匹配 | 12 | 新规则下的岗位级证据匹配 |
| 需核验匹配 | 556 | 保留机会但不承诺资格 |
| 质量门禁 | needs_attention | 地点标准化完整率不足 90% |

这些指标说明专业误标风险下降，但“全国大量国内岗位”尚未完成；岗位规模仍受官方入口可访问性和适配器数量限制。

## Source evidence

- 中海油服官方首页：`https://www.cosl.com.cn/`
- 官方首页公开招聘 ATS：`http://cosl.zhiye.com/`
- 本机复测：ATS 返回 502/TLS 失败，故 `cosl-career` 保持禁用，不能由此推断岗位为零。

## Responsive checks

- 桌面端 1280px：页面正常渲染，日报日期为 `2026年9月24日`，专业下拉框包含本科资源勘查工程、硕士/博士三类目标专业。
- 移动端 390×844：菜单按钮、日报统计、专业筛选和主要入口均可见，无横向溢出；截图见 `screenshots/home-mobile-390.png`。
- 本阶段无前端布局改动，桌面截图见 `screenshots/home-desktop-1280.png`。

