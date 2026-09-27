# Phase 53 交付说明

## 分支与版本

- 分支：`phase/53-cmgb-browser-worker`
- 建议标签：`v0.22.42`
- 审阅标签：`phase-53-review`

## 交付范围

本阶段交付的是国聘动态采集器的可维护实现和发布门禁，不是一次真实云服务器采集结果。学生端前端未改动，Phase 52 桌面端、390px 手机端和岗位详情截图继续作为视觉回归基线：

- `docs/phase-52/screenshots/home-desktop-1280.png`
- `docs/phase-52/screenshots/home-mobile-390.png`
- `docs/phase-52/screenshots/job-detail-cmgb-1155.png`

## 下一阶段准入

只有服务器首轮动态捕获通过本阶段门禁，才允许进入下一阶段的三桶油下属单位、事业编和公务员职位表扩展。否则继续修复浏览器捕获或来源网络，不把失败结果写成岗位或“无岗位”。

