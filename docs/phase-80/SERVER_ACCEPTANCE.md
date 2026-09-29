# Phase 80 服务器验收记录

部署前置：保留 Phase 79 工作区和数据库备份，使用独立工作区构建新镜像。验收命令：

```bash
docker compose exec -T worker python -c "import docx; print(docx.__version__)"
docker compose exec -T worker python -m job_hub.cli discover-configured-government-artifacts
docker compose exec -T worker python -m job_hub.cli audit
```

验收时记录：

- DOCX 依赖版本和容器健康状态；
- 新发现/重新提取的附件数量；
- 新建、拒绝、待复核候选数量；
- 当前开放匹配岗位数量；
- 来源故障与扫描成功无匹配的分类。

DNS 仍属于外部待办；`jobs.cupdky.cn` 未恢复前，不能把系统标记为正式公网发布。
