# Phase 7 迁移说明

## 数据库

本阶段没有 SQLite schema 迁移。新增适配器配置和探测证据均为版本化 JSON；已有岗位、来源健康、抓取运行和日报数据结构不变。

回退到 `phase-6-review` 不需要执行数据库降级。生产回退前仍应备份 `APP_DATABASE_PATH` 和 `APP_DATA_DIR`。

## 配置与权限

- 中国海油来源的 `enabled` 保持为 `false`。
- 新增管理员接口沿用 `X-Admin-Token`，不向公开 API 或学生页面暴露。
- 不新增凭据和环境变量。

## 回退检查

```powershell
git switch phase/6-national-energy-source-matrix
python -m pytest -q
```
