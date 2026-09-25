# Phase 32 质量报告

日期：2026-09-25  
分支：`phase/32-cnpc-official-matrix`

## 矩阵与岗位审计

```text
单位总数：13
有岗位级官方样例的单位：11
只有官方身份/入口、暂未绑定岗位的单位：2
备用官方入口覆盖率：100%
快照岗位行：20
成功绑定具体单位：20/20
明确包含学院四个目标专业的岗位：20/20
缺字段：0
非官方证据 URL：0
未绑定岗位：0
```

## 自动化验证

```text
python -m pytest -q
252 passed

python -m compileall -q job_hub
passed

python -m job_hub.cli cnpc-matrix
snapshot_contract_passed: true
```

管理员接口 `/api/admin/cnpc-matrix` 未带令牌返回 403，带管理员令牌返回 200。学生端不会因该接口暴露单位矩阵或未核验入口。

## 当前边界

本阶段完成的是中石油单位和岗位证据的可重复审计，不宣称已经打通中石油动态平台的无人值守实时采集。官方平台在维护、详情接口为空或访问策略变化时，必须继续保留 `access_limited`/人工复核状态；下一步接入公开浏览器捕获或职位表附件后，仍需通过本矩阵绑定。
