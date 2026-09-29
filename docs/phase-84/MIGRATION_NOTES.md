# Phase 84 迁移说明

- 无 SQLite 结构迁移。
- 无学生端数据迁移、回填或清退。
- 国聘浏览器失败诊断从单一 `captures/<name>.failure.json` 扩展为不可覆盖的 `captures/<name>.failures/<timestamp>-<status>.json`；原单一文件保留为最新诊断副本，便于现有运维命令查看。
- 正式 `captures/<name>.json` 现在是成功且完整捕获专用路径。升级不会主动修改已有文件；下一次 worker 运行才会按新规则写入。
