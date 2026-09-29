# Phase 82 服务器验收

验收时必须记录：

- `cmgb-browser` 重启后 heartbeat 能从 `starting` 进入 `capturing` 或 `degraded`；
- 超时/失败文件不覆盖上一次成功 `captures/cmgb-iguopin-browser.json`；
- Web、Worker 和数据库审计保持健康；
- 动态捕获不完整时学生端岗位数量不减少为零。

正式域名 DNS/HTTPS 仍属于外部待办，不能由本阶段标记 `public_ready=true`。

## 本轮真实结果（2026-09-29）

- `web`：healthy；`worker`：healthy。
- Linux 容器内 `_capture_deadline(1)` 实测抛出超时异常，运行时限生效。
- `audit`：`ok=true`，1501 条岗位、206 条当前开放岗位、0 个启用来源失败。
- CMGB 浏览器本轮在 CDP 连接阶段超时，heartbeat 为 `degraded`；失败原因已记录，既有成功捕获文件未被覆盖，学生端岗位未被清空。
- 该结果属于动态来源故障，不计作“扫描成功无匹配”，也不新增岗位数量。
