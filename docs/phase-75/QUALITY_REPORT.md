# Phase 75 质量报告

## 风险与修复

| 风险 | 修复后行为 |
| --- | --- |
| 附件岗位的专业、学历匹配，但同一行“任职资格”要求多年经验 | 不进入学生端审核候选，标记为 `需工作经验` |
| 整行文本含地质关键词 | 不用于专业匹配；专业门禁仍只读取明确专业字段 |
| 同一岗位明确写明应届毕业生可投递 | 保留既有例外，不因经验表述误删 |
| 成熟人才、仅面向在职人员的官方公告 | 只能作为私有发现/审核对象，不能按单位或关键词自动公开 |

## 自动化验证

已执行：

```text
python -m pytest -q: 369 passed
python -m compileall -q job_hub: passed
docker compose config --quiet: passed
docker compose -f docker-compose.browser.yml config --quiet: passed
git diff --check: passed
```

新增回归覆盖：

- `test_publication_gate_reads_experience_from_one_attachment_row`：同一附件行中的三年工作经验会阻断发布；
- `test_attachment_row_requiring_experience_never_enters_student_review_queue`：专业和学历匹配不足以绕过经验门禁。

## 数据边界

本阶段没有把“成熟人才”公告、过期自然资源部公告或尚未开始报名的中国地震局职位增加到当前在招统计。真实岗位数量只能在生产 Worker 成功复核官方证据后增长。

## 界面验证

本阶段未修改模板、CSS、JavaScript、公开路由或 API 响应结构，因此不存在需要重新验收的桌面/手机视觉变更。附件岗位在通过同一既有公开门禁后才会影响学生端列表；本阶段新增的回归测试覆盖了该数据路径。
