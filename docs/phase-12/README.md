# Phase 12：国内官方岗位采集引擎

本阶段围绕“中国石油大学（北京）地球科学学院学生能够看到更多真实国内岗位”推进采集链路，重点处理中国石油大学（北京）就业信息网（CUPB）这一公开聚合入口，并保留三桶油、地勘、能源设计院、科研院所和高校招聘公告的官方原文证据。

## 本阶段完成

- 解码 CUPB 公开页面中的 Base64 + zlib 压缩正文。
- 保留 CUPB 官方 `//page/N` 分页路由，增加多入口、有限分页和候选优先级。
- 对同一公告的 `campus/view`、`news/view`、`job/view` 镜像去重。
- 从公告标题和正文识别真实招聘单位，避免把就业网发布账号当作用人单位。
- 正文的工作地点、注册地、坐落地和通讯地址优先于平台默认地点。
- 登记四个管理员核验的官方详情页作为列表页不可达时的受控备用入口。
- 完成 100 人地球科学学院本科、硕士、博士模拟和覆盖质量报告。

## 明确边界

CUPB 列表页在本机本次复测返回 0 条候选，属于当前网络/页面访问状态，不代表官网没有岗位。备用详情页仅使用之前保存的公开 HTTP 200 响应回放，不绕过 robots、登录、验证码或访问控制。中国石油、中国石化、国家管网统一招聘入口的 403/412/TLS 限制仍需在服务器直连环境复测，并继续从下属单位正式公告和职位表扩展。

## 验收命令

```powershell
python -m pytest -q
python -m compileall -q job_hub tests
python -m job_hub.cli audit
python -m job_hub.cli coverage --output runtime/phase12-final-coverage.json
python -m job_hub.cli simulate-cohort --output runtime/phase12-final-cohort.json
git diff --check
```
