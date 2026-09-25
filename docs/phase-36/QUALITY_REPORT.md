# 质量报告

## 本地验证

```text
python -m pytest -q                         259 passed
python -m job_hub.cli government-position-audit --today 2026-09-26
  records=2
  verified_open_records=2
  explicit_student_matches=2
  location completeness=100%
  official evidence completeness=100%
```

## 发布门禁

- 事业编岗位：专业、学历、地点、截止日期、官方公告、官方附件和表格行定位齐全。
- 附件原始文件：服务器下载并保存 SHA-256，不向学生端暴露私有存储路径。
- 公务员：当前年度官方职位表未确认，0 条当前公务员岗位是“尚未取得官方职位表”的结果，不是“全国没有公务员岗位”。
- 中国石油：00:00–06:00 维护窗口继续按访问受限处理，本阶段不修改 CNPC 采集器。

## 服务器复核

```text
docker compose exec web python -m job_hub.cli audit
  ok=true; checked_jobs=935; open_jobs=201; issues=[]
docker compose exec web python -m job_hub.cli government-position-audit --today 2026-09-26
  verified_open_records=2; explicit_student_matches=2; location completeness=100%
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
  ok=true
```

线上岗位 `/jobs/934` 和 `/jobs/935` 均返回 200，包含“合肥市”和专业/学历/截止日期字段。
