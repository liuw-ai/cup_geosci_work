# Phase 81 官方附件批量处理队列

Phase 80 让 Word 岗位表可以按表格逐行解析，但每日 Worker 仍只读取默认 100 条、且只处理 `registered` 状态。附件规模扩大后，较早登记的官方文件可能被新文件挤出队列，下载中断后也不能自动续提取。

本阶段将附件处理变成可控的批量队列：

- `registered` 和已下载但未提取的 `downloaded` 附件按最早更新时间优先处理；
- 单轮处理数量由 `ATTACHMENT_PROCESS_BATCH_LIMIT` 限制，默认 500，避免阻塞来源同步；
- `process-pending-artifacts` 提供管理员手动补处理入口，可按来源过滤；
- 失败附件只有显式传入 `--retry-failed` 才会重试，robots/访问策略限制的 `skipped` 不会被盲目重试；
- 处理结果只进入私有原始行和岗位候选复核队列，绝不绕过岗位级证据和学生端发布门禁。

## 运维命令

```bash
python -m job_hub.cli process-pending-artifacts --limit 500
python -m job_hub.cli process-pending-artifacts --retry-failed --source-id hubei-natural-resources
```

命令返回所选数量、提取行数、候选数量、跳过数和失败数。存在失败时命令以非零状态退出，便于服务器监控发现问题。

## 不在本阶段范围

- 不把解析成功的行自动发布到学生端；
- 不重试 robots、验证码、登录或访问策略受限来源；
- 不把历史截止附件重新标记为当前在招。
