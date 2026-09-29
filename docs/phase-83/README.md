# Phase 83 省级官方入口分流

全国 31 省矩阵中已经定位了许多自然资源、地质局/地质院入口，但“有一个官方 URL”不等于该 URL 当前可被服务器合规读取，更不等于它存在地学院学生可投的岗位。本阶段把这部分候选入口变成可复跑的只读探测任务，优先缩小事业编和省级岗位扩源的真实瓶颈。

## 交付

- 新增 `provincial-entry-probe` 运维命令；
- 默认只探测 `candidate` 与 `blocked` 状态、且已登记 `official_entry_url` 的省级矩阵目标；
- 每个目标独立核验 `robots.txt`、正式栏目 HTTP 状态、软 404 与栏目路径稳定性；
- 输出 `entry_accessible`、`access_limited`、`source_unavailable` 三种结果以及下一步动作；
- 探测结果不会写入数据库，不修改 `source_targets.json`，不启用来源，不发现附件，更不会发布岗位；
- 新增回归测试，保证候选和受限入口可以被区分，未定位和已启用来源不会被误探测。

## 服务器使用

在部署工作区运行：

```bash
docker compose exec -T worker python -m job_hub.cli provincial-entry-probe \
  --output /var/lib/job-hub/reports/provincial-entry-probe-$(date +%F).json
```

可先从一个省或一个角色开始，避免对官网施加不必要的访问压力：

```bash
docker compose exec -T worker python -m job_hub.cli provincial-entry-probe \
  --province 山东 \
  --role natural_resources \
  --output /var/lib/job-hub/reports/shandong-natural-resources-probe.json
```

只有 `entry_accessible` 的结果才进入下一步人工审阅：确认当前招聘公告、正式附件、报名期、岗位级专业/学历/地点/人数证据和回归样例，随后才允许把它配置为正式来源。`access_limited` 与 `source_unavailable` 必须保留为来源状态，绝不能显示为“本省无岗位”。

## 不在本阶段范围

- 不把入口可访问性当作岗位数量或专业匹配结论；
- 不自动绕过 robots、403、412、登录、验证码或目标站点访问策略；
- 不导入历史公务员职位表，也不把公务员年度入口误写成当年度在招；
- 不修改学生端岗位或过期清退规则。
