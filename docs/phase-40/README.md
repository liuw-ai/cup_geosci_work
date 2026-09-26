# Phase 40：中国石油公告与岗位详情浏览器采集

## 目标

中国石油招聘平台不是“列表一条记录对应一个岗位”。公开页面先分页列出油田、研究院、工程技术单位的招聘公告，进入公告详情后才有多行岗位表。Phase 40 增加了这一层专用采集链路：

```text
CNPC 官方列表分页
        |
        v
公告详情逐条打开
        |
        v
岗位表字段提取（岗位、专业、学历、地点、人数、截止日期）
        |
        v
完整捕获校验 -> 现有专业/学历/截止日期/官方证据门禁 -> 学生端
```

## 新增内容

- `job_hub/cnpc_browser_runner.py`
  - 只读访问中国石油公开招聘页面。
  - 以官方 `recruitInfoshow.html?id=...` 详情链接作为公告身份。
  - 通过表头匹配提取岗位表，避免把列顺序写死。
  - 任一公告详情失败即生成 `partial` 捕获，不发布本轮岗位。
- `job_hub/cnpc_browser_capture.py`
  - 新增两层捕获契约和新鲜度、分页、详情、证据、重复 ID 校验。
- `job_hub/sources.py`
  - 新增 `cnpc_browser_rows` 适配器，接入普通岗位发布门禁。
- `job_hub/cnpc_browser_worker.py`
  - 独立浏览器 Worker，避开官方 `00:00-06:00` 维护窗口，默认每 3 小时捕获一次。
- `docker-compose.browser.yml` / `Dockerfile.browser`
  - Chromium 与普通 Web/Worker 镜像分离，避免网站服务携带浏览器依赖。
- `data/sources.json`
  - 新增 `cnpc-career-browser` 官方来源。它只有在捕获文件完整且新鲜时才会产生岗位；原有 `cnpc-career` 官方快照仍保留。

## 运维命令

浏览器 Worker 与普通服务使用同一 `job_hub_data` 卷：

```bash
docker compose up -d --build
docker compose -f docker-compose.browser.yml up -d --build
docker compose exec worker python -m job_hub.cli audit
docker compose exec worker python -m job_hub.cli coverage --record
```

单次检查捕获文件：

```bash
docker compose exec worker python -m job_hub.cli cnpc-job-capture-check \
  --path /var/lib/job-hub/captures/cnpc-jobs.json
```

浏览器 Worker 需要能够访问中国石油公开页面；不安装浏览器或页面被官方访问策略限制时，Worker 只记录心跳和失败原因，不绕过 robots、验证码、登录或访问限制。

## 发布门禁

以下任何一项不满足，岗位不会进入学生端：

- 列表所有分页已完成；
- 所有目标公告详情均已访问；
- 岗位表有岗位名称、专业、学历、地点和报名截止日期；
- 官方详情 URL 和字段证据 URL 均在中国石油官方域名；
- 截止日期未过期；
- 既有地球科学学院专业匹配和岗位相关性门禁通过。

“partial”“access_limited”“stale”均不是“扫描成功但无匹配”，而是来源受限状态，保留在管理员审计中。

