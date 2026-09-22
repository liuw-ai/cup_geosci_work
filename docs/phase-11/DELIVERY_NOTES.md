# Phase 11 交付说明

## 已完成

- [x] 公告正文误过滤修复
- [x] 同一官方公告按明确岗位段落拆分
- [x] 中国冶金地质总局地球物理勘查院真实来源接入
- [x] `.xls` / 误标 OOXML 职位表解析
- [x] 安徽官方职位表进入私有审核队列
- [x] CUPB 招聘公告列表扩展扫描与正文专业证据修复
- [x] 共享原文链接审计规则修正
- [x] 国内来源串行直连复测
- [x] 自动化测试、静态检查、覆盖报告和页面截图
- [x] 390px 手机端无横向溢出复核（报告说明可换行，页面宽度与视口一致）
- [x] 独立 Git 分支与阶段 tag

## 验收命令

```powershell
python -m pytest -q
python -m compileall -q job_hub tests
git diff --check
$env:HTTP_TRANSPORT_MODE='direct'
python -m job_hub.cli audit
python -m job_hub.cli coverage --output runtime/phase-11-coverage-final.json
python -m job_hub.cli simulate-cohort --output runtime/phase-11-cohort-final.json
```

截图：`screenshots/home-desktop-1280-final.png`、`screenshots/home-mobile-390-final.png`。

## 审阅边界

本阶段提交后等待审阅，不自动合并 `master`。下一阶段优先处理：

1. 人工复核并决定是否发布安徽职位表中的明确匹配行。
2. 为中国石油、中国石化、国家管网下属单位逐一寻找公开公告/职位表，而不是继续撞击统一入口。
3. 为 31 省五类来源补当前栏目、样例公告和备用入口。
