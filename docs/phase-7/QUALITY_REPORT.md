# Phase 7 质量报告

日期：2026-09-22
分支：`phase/7-cnooc-public-adapter`

## 真实性与覆盖边界

| 指标 | 结果 | 解释 |
| --- | ---: | --- |
| 新增采集器 | 1 | `zhaopin_campus`，用于公开校招接口 |
| 真实探测入口 | 1 | 中国海油 2026 届校招页面 |
| 接口 HTTP 状态 | 200 | 只代表网络响应到达 |
| 接口业务 code | 500 | 接口转换失败，来源不可发布 |
| 线上新增岗位 | 0 | 没有将失败响应当作岗位或无匹配 |
| 来源启用数变化 | 0 | 中国海油继续停用 |

## 适配器回归规则

- `code=200` 且 `totalNum=0` 才能返回成功空结果。
- `code=500`、非 JSON、缺少 `jobList/pageInfo` 或分页不一致都会抛出采集错误。
- 岗位详情、专业证据和官方白名单链接缺一不可。
- 业务错误不进入学生端，也不写成“扫描成功无匹配”。

## 自动化检查

以下检查已在 2026-09-22 提交前执行，均通过：

```text
python -m pytest -q                         -> 128 passed in 14.57s
python -m compileall -q job_hub tests        -> passed
python -m json.tool data/sources.json        -> passed
python -m json.tool data/national_source_matrix.json -> passed
python -m json.tool data/national_source_probes.json -> passed
git diff --check                             -> passed
docker compose config --quiet                -> passed
```

针对适配器的定向回归为 20 个测试，覆盖业务错误、严格空结果、专业过滤、官方链接白名单、活动编号缺失、契约校验和管理员证据接口，结果为 `20 passed`。

说明：Compose 文件按部署约定引用本地 `.env`，仓库不提交该文件。校验时临时复制 `.env.example` 作为占位配置，`docker compose config --quiet` 通过后立即删除临时文件；未使用或写入任何真实凭据。

## 页面复核证据

本阶段只增加采集器、探测证据和管理员接口，没有修改学生端前端代码。为避免重复生成并造成版本误解，以下文件是 Phase 6 已验证页面的原样复用副本：

| 视口 | 文件 | 尺寸 | 复核结果 |
| --- | --- | --- | --- |
| 桌面 | [home-desktop-1280.png](screenshots/home-desktop-1280.png) | 1280 x 900 | 页面正常渲染，布局无重叠 |
| 手机 | [home-mobile-390.png](screenshots/home-mobile-390.png) | 375 x 812 | `clientWidth=375`、`scrollWidth=375`，无横向溢出 |
| 手机菜单 | [home-mobile-menu-open-390.png](screenshots/home-mobile-menu-open-390.png) | 375 x 812 | 菜单 `aria-expanded` 从 `false` 变为 `true`，内容可见 |

图片来源及复用关系记录在本阶段交付说明中；本阶段未声称产生新的前端视觉改动。

## 剩余风险

1. 中国海油接口当前业务失败，尚未形成可发布岗位样例。
2. Zhaopin 页面每个招聘年度可能更换公司编号、脚本文件和岗位链接字段，需重新探测。
3. 其他三大体系仍需分别开发公开公告、职位表或合规 API 适配器。
4. 本阶段没有把 `curl_cffi`、Firecrawl 或 Scrapling 用于绕过限制；它们不能替代官方证据和站点逐项核验。
