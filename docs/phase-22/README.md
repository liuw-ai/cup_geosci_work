# Phase 22：动态官方门户捕获与事业单位扩展基线

## 本阶段完成

本阶段把动态招聘门户从“只能保留人工快照”推进为可部署的服务器捕获链路，同时保持学生端的真实性门禁：

- 新增 `official_browser_rows` 来源类型。服务器浏览器生成的清单必须包含完整分页计数、岗位级官方详情 URL、专业、学历、地点、截止日期和字段证据。
- 新增 `browser-capture-run` 与 `browser-capture-check` 运维命令。执行器只访问公开页面，先核验 `robots.txt`，不登录、不调用报名接口、不绕过验证码或访问策略。
- 新增 `pipechina-browser-capture` 注册项。它默认停用，直到部署服务器完成 Chromium、robots、分页和字段选择器复测；现有 `pipechina-career` 2026-09-24 官方快照继续作为学生端来源。
- 捕获文件有有效期（默认 30 小时），过期、分页不完整、字段缺失、非官方详情域名或访问受限均会失败，不会变成“无岗位”。
- 继续保留中国冶金地质总局第二地质勘查院、内蒙古地质勘查院等已核验官方事业单位/地勘来源；没有找到当前有效的国家公务员职位表时不新增虚构岗位。

## 服务器启用条件

浏览器执行器依赖部署环境自行安装的 Playwright/Chromium。核心 web/worker 镜像不强制携带浏览器，避免普通部署体积和攻击面扩大。启用前需要在服务器：

1. 安装 Playwright 与 Chromium。
2. 用 `browser-capture-run pipechina-browser-capture` 做一次只读捕获。
3. 用 `browser-capture-check pipechina-browser-capture` 验证捕获清单新鲜、分页完整、岗位证据完整。
4. 先人工查看结果，再将来源 `enabled` 改为 `true`，并保留旧快照作为回退。

示例：

```bash
python -m job_hub.cli browser-capture-run pipechina-browser-capture
python -m job_hub.cli browser-capture-check pipechina-browser-capture
python -m job_hub.cli sync-source pipechina-browser-capture
python -m job_hub.cli audit
```

如果返回 `Playwright is not installed`、`robots.txt` 错误、分页不完整或字段选择器缺失，说明动态来源仍不可自动采集；这时不要启用来源，系统继续使用已核验快照并在覆盖报告中标记访问受限。

## 真实来源边界

本阶段没有把中公、华图、公众号或搜索结果写入学生端，也没有把国家公务员年度职位表缺失解释为“无岗位”。公务员和省级事业编下一批必须以政府/单位官网公告及 PDF/Excel 职位表为证据，逐岗位通过专业和学历门禁后再发布。
