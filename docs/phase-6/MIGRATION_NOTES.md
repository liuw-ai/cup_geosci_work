# Phase 6 迁移说明

## 数据库

本阶段没有 SQLite 迁移。新增内容全部位于版本化 JSON 准入台账和纯函数读取模块；现有岗位、日报、来源健康、抓取运行、附件和证据数据不变。

因此回退到 `phase-5-review` 或更早版本不需要还原数据库列。服务器只需回退代码和静态数据文件即可。

## 配置与权限

- 新增的管理员接口沿用 `X-Admin-Token`，学生端和公开 API 不返回矩阵 URL、错误细节或未验证入口。
- 不新增环境变量，不需要新凭据。
- CLI 只读矩阵和本地数据库，不主动联网。

## 回退检查

```powershell
git switch phase/5-discovery-verification
python -m pytest -q
```

由于没有 schema 迁移，回退不会删除或改写岗位数据。生产部署前仍应备份 `APP_DATABASE_PATH` 和 `APP_DATA_DIR`。
