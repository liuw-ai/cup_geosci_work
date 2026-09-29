# Phase 80 迁移说明

- 数据库结构无变化，无需迁移脚本。
- Docker 镜像重建会安装 `python-docx==1.1.2`。
- 已登记的 Word 附件可在服务器使用 `process-artifact <artifact_id>` 重新提取；原始附件哈希和旧解析记录保留。
- 重新解析产生的行只进入私有候选队列，管理员复核前不得公开。
