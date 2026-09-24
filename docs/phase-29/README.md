# Phase 29：同步任务心跳可靠性

本阶段修复长时间来源同步期间 Worker 心跳停滞的问题。同步任务现在按来源报告进度，Docker 健康检查可以区分“正在同步”和“已经失联”，不会把正常运行的长任务误判为不健康。

## 本阶段交付

- `job_hub/pipeline.py`：`JobPipeline.sync_all()` 支持可选的进度回调，在每个来源开始和完成时报告进度。
- `job_hub/worker.py`：Worker 将来源进度写入心跳；回调或心跳写入失败只记录日志，不改变采集结果。
- `tests/test_pipeline.py`：覆盖来源同步进度回调顺序和回调失败隔离。
- Docker Worker 健康检查继续使用 `worker-health --max-age 180`，但长同步会持续刷新心跳。

## 运行与验证

```bash
docker compose ps
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
docker compose exec worker python -m job_hub.cli audit
```

心跳健康只表示 Worker 进程仍在运行，不代表所有来源都成功。来源访问受限、解析失败和人工复核仍必须在审计结果中单独保留。

## 当前边界

本阶段没有新增招聘来源，也没有绕过目标站点的 robots、403、412、验证码或登录限制。中国石油、中国石化、国家管网和中国海油的专用适配器仍需逐站完成公开页面或附件证据核验。
