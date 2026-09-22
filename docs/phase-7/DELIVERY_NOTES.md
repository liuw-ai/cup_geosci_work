# Phase 7 交付说明

## 已交付

- [x] 独立分支：`phase/7-cnooc-public-adapter`
- [x] 公开 Zhaopin 校招接口适配器和严格业务错误边界
- [x] 中国海油真实入口/API探测证据
- [x] 探测证据契约、管理员 API 和 CLI
- [x] 专业过滤、官方链接白名单和空结果一致性测试
- [x] 无数据库迁移、可回退到 Phase 6
- [x] 全量测试、静态检查、JSON 契约检查、Compose 配置检查
- [x] 桌面/手机页面复核证据（沿用无前端变更的 Phase 6 截图并标注尺寸）
- [ ] 用户审阅通过后合并 `master` 并创建稳定版本标签

## 验证记录

- `python -m pytest -q`：128 passed in 14.57s。
- 定向适配器与探测测试：20 passed。
- `compileall`、三个 JSON 文件解析、`git diff --check` 和 `docker compose config --quiet`：全部通过。
- Compose 校验使用临时 `.env.example` 占位文件，校验完成后已删除；真实部署仍需由管理员在服务器创建 `.env`。
- 学生端没有新增内部接口地址或失败堆栈；探测详情仅由管理员 API/CLI 返回。
- 本阶段没有前端源代码变更，截图从 Phase 6 原样复制到 `screenshots/`，用于确认页面基线未回归。

## 审阅重点

1. 是否接受“适配器已完成但来源仍停用”的真实状态表达。
2. `code=500` 与 `totalNum=0` 的边界是否足够严格。
3. 下一批优先开发中国石油、中国石化、国家管网中的哪一种正式入口。
