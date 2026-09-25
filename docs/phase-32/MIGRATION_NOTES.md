# Phase 32 迁移说明

本阶段没有新增数据库表，也没有改变岗位表结构。新增 `job_hub/cnpc_matrix.py` 作为只读审计层：它读取现有 `data/domestic_source_expansion_queue.json` 和中石油官方快照，生成矩阵报告。

部署时只需同步代码和测试文件，然后重建 Web 容器。数据库无需迁移、无需删除历史岗位、无需重新导入快照。

管理员接口使用现有 `X-Admin-Token` 鉴权；学生端路由和公开数据不变。
