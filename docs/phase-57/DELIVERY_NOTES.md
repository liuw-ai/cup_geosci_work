# Delivery Notes

- 分支：`phase/57-government-freshness-gate`
- 目标标签：`phase-57-review`
- 本阶段不添加历史公务员岗位，也不把来源故障转换为无岗位。
- 服务器部署前必须保留现有数据库卷，并先执行审计与 worker-health。
- 回退方式：回到 `phase-56-review`，或回退本阶段提交；数据卷不需要删除。
