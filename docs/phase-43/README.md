# Phase 43：官方职位表到学生端发布桥接

本阶段修复一个真实的业务断点：官方政府职位表已经完成逐行核验，但原先只进入
管理员审计报告，没有走正常岗位索引，因此学生端看不到。现在每日 worker 会读取
版本化的 `government_position_registry.json`，把满足全部门禁的当前职位行转换成
普通岗位记录。

## 发布门禁

只有同时满足以下条件的行才会进入学生端：

- `record_status = verified_open`；
- `match_status` 为 `explicit_match` 或 `unrestricted_match`；
- 截止日期尚未到期；
- 官方公告、官方 PDF/XLS 附件、表格行定位、专业、学历、地点和人数证据齐全；
- 通过现有 `evaluate_student_publication` 专业和官方证据门禁。

当前安徽省地质矿产勘查开发局台账中的两条安徽工业经济职业技术学院博士岗位满足
上述条件，服务器同步后应显示为事业单位类别；山东、河南、天津已截止附件仍只作
历史解析证据。

## 每日链路

`worker -> 官方来源同步 -> government_position_registry -> 正常岗位规范化/去重 ->
截止日期清退 -> coverage/audit -> 20:00 日报`。

记录的 `external_id` 由台账记录 ID 和职位代码组成，重复同步只更新原记录，不会
生成重复岗位。台账中行被关闭或过期后，下一轮规范化会将对应岗位标记为过期，不再
出现在学生端。

## 验收

```bash
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
docker compose exec web python -m job_hub.cli audit
docker compose exec web python -m job_hub.cli coverage --record
```

验收重点是事业单位类别出现明确岗位、岗位详情保留公告和附件链接，以及专业/学历
字段完整，而不是单纯岗位总数增加。
