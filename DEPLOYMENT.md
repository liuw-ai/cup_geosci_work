# 部署与手机访问

> **Phase 7.1 当前边界（2026-10-23）**：旧的多 Compose/overlay 命令仅作为历史记录，不能直接执行。当前唯一生产入口仍是 [`deploy/compose.yaml`](deploy/compose.yaml)。Phase 7 的管理员认证、隐私统计和 Worker 自动化已完成代码与回归验收；正式公网长期运行仍必须通过 DNS、HTTPS、来源新鲜度、政府台账、日报和备份门禁。任何门禁未通过时，不得把 IP 临时入口当作正式服务，也不得绕过门禁发布旧快照。

本文是地学就业信息站的生产部署说明。生产环境建议使用 Linux 服务器、Docker Compose、一个已解析到服务器的域名和 Caddy/Nginx HTTPS 反向代理。

## 1. 运行边界

- `web` 只提供公开浏览页、只读 API 和受保护的管理员接口。
- `worker` 负责来源同步、数据审计、20:00 日报冻结和邮件通知。
- SQLite 数据库位于 Docker named volume `job_hub_data`，不要放在 NFS、SMB 或会产生锁语义差异的网络盘上。
- 每个正式 release 必须在 release 的 `.release.env` 中显式设置唯一的
  `JOB_HUB_DATA_VOLUME`。新 release 只能向这个新卷迁入经核验的数据库
  基线；不得复用上一版本的运行卷。旧卷只保留作回滚和审计。
- 采集器只读取白名单中的官方公开页面，并遵守 robots.txt；不绕过登录、验证码或反爬限制。

## 2. 首次部署

在项目根目录执行：

```bash
cp .env.example .env
```

至少修改以下变量：

```dotenv
APP_SECRET_KEY=使用密码管理器生成的长随机字符串
ADMIN_TOKEN=另一条长随机字符串
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=由 Werkzeug generate_password_hash 生成的哈希
ADMIN_ROLE=admin
APP_BASE_URL=https://jobs.cupdky.cn
MAIL_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=jobs@example.com
SMTP_PASSWORD=邮箱服务商生成的授权码
SMTP_FROM=jobs@example.com
SMTP_TO=管理员邮箱@example.com
SMTP_USE_SSL=true
HTTP_TRANSPORT_MODE=direct
WORKER_AUTOMATION_ENABLED=true
```

网页登录使用 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD_HASH`，不再使用 `ADMIN_TOKEN`。
`ADMIN_TOKEN` 只用于受保护的机器 API。生成密码哈希示例：

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('change-me'))"
```

生产发布不能修改基础 `.env` 中的开发默认值，也不能用可覆盖的镜像标签。镜像推送完成并取得各自的 `@sha256:...` 内容摘要后，生成不含密码或 SMTP 信息的独立发布清单：

```bash
python scripts/prepare_release_env.py \
  --version 2026.10.10-1 \
  --application-image registry.example/cupb-job-hub@sha256:<application-digest> \
  --browser-image registry.example/cupb-job-hub-browser@sha256:<browser-digest> \
  --browser-deps-image registry.example/cupb-job-hub-browser-deps@sha256:<deps-digest> \
  --headless-shell-image chromedp/headless-shell@sha256:<headless-shell-digest>
```

该命令默认写入 `.release.env`，其中应用镜像摘要会同时作为 `APP_RELEASE_IMAGE_SHA` 和浏览器运行时基础镜像。`release-check` 要求发布版本、Git SHA、构建时间和全部五个镜像引用均有效，且只接受 `@sha256:` 内容摘要；普通固定标签和 `latest` 都不能通过生产门禁。

`HTTP_TRANSPORT_MODE` 默认是 `environment`，会遵循服务器进程环境中的代理变量。只有服务器具备组织允许的稳定直连公网出口时才设置为 `direct`；它的作用是避免把本机/容器代理握手失败误判为“无岗位”，不是绕过目标站点的 403、412、验证码或 robots 规则。两种模式都保留 TLS 证书校验。

`APP_BASE_URL` 必须是学生在微信中实际打开的 HTTPS 地址；不能填写 `127.0.0.1`、`localhost`、服务器内网地址或 IP HTTP 地址，否则日报邮件中的链接会指向错误位置，且管理员会话无法安全工作。

管理员初始密码只用于首次登录，不能写入 Git、发布清单或聊天记录。正式域名和 HTTPS 验证通过后，必须在服务器 secret 环境文件中生成新的 Werkzeug 哈希并重启 `web`，同时使旧密码失效：

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash(input('New admin password: ')))"
```

密码轮换后应删除终端历史中的明文输入，并重新验证管理员登录、注销、CSRF 和角色权限。当前 IP HTTP 入口不接受管理员登录。

先做静态校验，再启动服务：

```bash
docker compose --env-file .release.env -f deploy/compose.yaml config
docker compose --env-file .release.env -f deploy/compose.yaml run --rm web python -m job_hub.cli release-check
docker compose --env-file .release.env -f deploy/compose.yaml up -d --no-build
docker compose --env-file .release.env -f deploy/compose.yaml ps
docker compose --env-file .release.env -f deploy/compose.yaml logs --tail=100 worker
```

`production-readiness` 还会检查所有启用非手工来源的捕获新鲜度。输出中的
`freshness_unproven_sources`、`stale_capture_sources` 或
`invalid_capture_sources` 只要有一项非空，就不能把系统标记为可正式服务；必须让对应
worker 完成真实成功捕获，不能通过修改 `max_age_hours` 或重启服务绕过。

浏览器 Worker 不仅抓取证据，还会直接同步岗位，因此不能只替换 Web/主 Worker 的应用镜像。
每次调整专业匹配、岗位发布或证据规则，必须用同一应用运行时重建并逐个替换所有启用的浏览器 Worker；
`production-readiness.browser_workers.workers` 的 `policy_consistent` 全部为 `true`
才算规则同版。旧 Worker 即使 Docker healthcheck 显示 healthy，也不能通过发布门禁。

worker 遇到备份、同步或政府证据复核异常时，会保持 `degraded` 心跳，并按
`WORKER_FAILURE_RETRY_SECONDS`（默认 60 秒）重试；不会把一次失败伪装成正常运行，
也不会等到完整的三小时来源周期才重试。只有成功恢复后才恢复 `running` 心跳。

worker 还会每 `DOMESTIC_ENTRY_HEALTH_INTERVAL_MINUTES` 分钟（默认 360）复核扩源
台账中 `official_identity_only` 的官方入口，输出到
`/var/lib/job-hub/domestic-entry-health/latest.json`。该检查只验证 robots 和入口
可达性：入口可达只意味着可继续定位岗位样例，入口失败也不等于没有岗位；它不会注册
来源、采集岗位或修改学生端数据。

浏览器采集服务还配置了独立的 Docker healthcheck。它只读取该容器对应的
`BROWSER_WORKER_SERVICE_NAME` 心跳：进程卡死或心跳超过 4 小时会触发
`restart: unless-stopped` 的自动恢复；robots/403/412 等已记录的来源访问限制会保持
`degraded`，不会被误当作进程故障反复重启。恢复后仍必须完成真实捕获，来源新鲜度门禁
不会因容器重启而放行旧快照。

上线前建议在服务器执行一次国家能源入口的只读直连诊断，并把结果保存到运行目录：

```bash
docker compose -f deploy/compose.yaml run --rm \
  -e HTTP_TRANSPORT_MODE=direct web \
  python -m job_hub.cli national-entry-probe \
  --transport direct --environment production-server \
  --output /var/lib/job-hub/national-entry-probe-direct.json
```

诊断只检查 `robots.txt` 和登记的公开入口。`access_policy_block`、`robots_blocked`、`source_unavailable` 都表示不能自动采集，不能在日报中写成“当地无岗位”。

Compose 启动时 worker 会自动注册来源并执行同步。需要手动初始化或排查时，可在共享卷中执行：

```bash
docker compose -f deploy/compose.yaml run --rm web python -m job_hub.cli init
docker compose -f deploy/compose.yaml run --rm web python -m job_hub.cli audit
docker compose -f deploy/compose.yaml exec worker python -m job_hub.cli worker-health --max-age 180
```

## 3. HTTPS 反向代理

生产部署继续使用现有独立 Caddy 项目。应用端口只对服务器本机开放，公网流量由 Caddy 终止 TLS 后转发到共享 Docker 网络中的 `web:8080`。示例配置见 [`deploy/Caddyfile.example`](deploy/Caddyfile.example)。正式域名生效前，IP 入口只允许 308 重定向到正式 HTTPS 主机，不再把应用内容或管理员登录表单通过明文 HTTP 代理出去。切换 release 时不重建 Caddy，只切换共享网络中的 Web 服务。

本项目固定使用 `jobs.cupdky.cn`。先确认 `cupdky.cn` 已注册且已在域名服务商或 DNS 托管商处启用权威 DNS；若 `nslookup cupdky.cn` 本身返回 `NXDOMAIN`，仅添加 `jobs` 记录不会生效。根域可解析后，创建 `A` 记录：`jobs` -> `81.70.62.174`（TTL 设为 `600` 或服务商的 `Auto`）。确认 `nslookup jobs.cupdky.cn` 返回该地址后，在服务器执行：

```bash
docker compose --env-file .release.env -f deploy/compose.yaml up -d --no-build
docker compose -f /opt/cup_geosci_work_phase442/docker-compose.public.yml up -d
docker compose -f /opt/cup_geosci_work_phase442/docker-compose.public.yml logs --tail=100 caddy
```

部署后先运行只读域名预检：

```bash
docker compose --env-file .release.env -f deploy/compose.yaml exec web python -m job_hub.cli domain-check \
  --hostname jobs.cupdky.cn --expected-ip 81.70.62.174
```

该命令的退出码只有在 DNS 已解析到目标地址且 `https://jobs.cupdky.cn/healthz`
返回成功时才为 0。`nxdomain` 表示域名服务商还没有创建记录，`wrong_target`
表示 A 记录指向了其他地址，`tls_error` 表示证书尚未签发或证书链错误；这些状态
都与应用进程和招聘来源采集无关。

防火墙只需要对外开放 80 和 443；不要把 SQLite、管理员接口或 8080 直接暴露到公网。DNS 生效后 Caddy 会自动申请和续期公开证书，微信浏览器可以直接打开标准 HTTPS 链接。DNS 未生效时 Caddy 会保持重试，不能用 IP 地址申请 `jobs.cupdky.cn` 的证书。

若使用 Nginx，等价的核心配置是 `proxy_pass http://127.0.0.1:8080;`，并保留原始 `Host`、`X-Forwarded-Proto` 和 `X-Real-IP` 请求头。

## 4. 为什么手机端访问不到

最常见的原因不是页面本身，而是访问地址和监听范围不对：

1. 在手机中打开 `http://127.0.0.1:8080` 时，`127.0.0.1` 指向手机自己，不是服务器。
2. Compose 的本机绑定只允许服务器上的反向代理访问 8080；手机应该访问 `https://jobs.example.edu.cn`。
3. 若只是同一局域网临时测试，应让 Flask/Gunicorn 监听 `0.0.0.0`，用服务器局域网 IP 加端口访问，并放行服务器防火墙；这种方式不适合作为公开生产入口，也可能被微信的明文 HTTP 限制拦截。
4. 公网访问还需要检查 DNS、云安全组、服务器防火墙和 HTTPS 证书是否都已生效。

快速排查顺序：

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -I https://jobs.example.edu.cn/healthz
nslookup jobs.cupdky.cn
docker compose -f /opt/cup_geosci_work_phase442/docker-compose.public.yml ps
docker compose --env-file .release.env -f deploy/compose.yaml ps
docker compose --env-file .release.env -f deploy/compose.yaml logs --tail=100 web worker
```

第一条失败说明容器或 Gunicorn 没有正常工作；本机 Web 成功而域名失败，说明是 DNS、Caddy 或防火墙链路问题；DNS 返回 NXDOMAIN 时必须先到域名服务商添加记录，代码和服务器无法替代 DNS 所有权操作。

## 5. 每日 20:00 链路

worker 会按 `SOURCE_SYNC_INTERVAL_MINUTES` 周期刷新来源，并在每次周期中自动幂等登记 `GOVERNMENT_ARTIFACT_MANIFEST_PATH` 指向的事业编/公务员官方附件清单，再进入受控附件处理队列。生产默认 `DEFAULT_SOURCE_MAX_AGE_HOURS=72`：启动时会为没有来源级配置的实时官方来源物化新鲜度合同，成功同步会写入捕获时间；超过窗口未成功更新的岗位由读侧和生命周期清理同时 fail-closed。静态附件、浏览器快照仍必须具备独立捕获证据，不会用 worker 心跳或同步时间冒充成功捕获。到达 `DAILY_PUBLISH_TIME=20:00` 后，它会再次同步、执行发布前审计、冻结当天日报，并通过 SMTP 发送日报链接。服务器在 20:00 短暂离线时，worker 重启后会检测当天是否缺少日报并补发。

健康检查要求：

- `/healthz` 返回成功，说明 web 进程可用。它是 Docker 的存活检查，不代表岗位数据已经可以正式发布。
- `/readinessz` 返回 HTTP 200 才表示内部生产门禁通过；worker、来源捕获新鲜度、政府附件证据、日报、备份或连续验证任一失败时返回 HTTP 503。该接口只返回最小状态，详细原因使用 `production-readiness` 命令查看。
- `worker-health --max-age 180` 返回 `ok: true`，说明 worker 最近有心跳。
- 邮件发送失败不会修改已经冻结的日报内容；worker 会记录失败并在下一轮尝试重发。

## 6. 上线后的质量基线

首次上线、调整来源配置或更新解析器后，应先在服务器上建立一份可追溯的质量基线：

```bash
docker compose --env-file .release.env -f deploy/compose.yaml exec web python -m job_hub.cli audit
docker compose --env-file .release.env -f deploy/compose.yaml exec web python -m job_hub.cli coverage --record --output /var/lib/job-hub/coverage.json
docker compose --env-file .release.env -f deploy/compose.yaml exec web python -m job_hub.cli simulate-cohort --output /var/lib/job-hub/cohort-coverage.json
```

`audit` 必须通过，才允许冻结日报。`coverage --record` 将本日省级来源状态、字段完整率、来源集中度和 100 人匿名画像匹配结果写入 SQLite；同日可被后续成功同步替换，页面趋势只与前一个不同日期比较。来源异常、尚未扫描或五类省级官方入口未全部核验时，零岗位只表示当前无法判断，不能对外写成“当地没有招聘”。

## 7. 备份与更新

更新代码前先备份数据库卷。最简单的做法是在服务器上停止写入后导出 SQLite：

```bash
docker compose --env-file .release.env -f deploy/compose.yaml exec web python -c "from job_hub.config import Settings; print(Settings.from_env().database_path)"
docker compose --env-file .release.env -f deploy/compose.yaml stop worker
docker compose --env-file .release.env -f deploy/compose.yaml cp web:/var/lib/job-hub/job_hub.sqlite3 ./backup/job_hub-$(date +%F).sqlite3
docker compose --env-file .release.env -f deploy/compose.yaml up -d worker
```

更新后依次运行 `docker compose --env-file .release.env -f deploy/compose.yaml config`、同一文件的 `up -d --no-build`、`audit` 和 `worker-health`。不要把 `.env`、数据库、SMTP 授权码或运行时日志提交到 Git。

## 8. 数据质量边界

岗位详情页保留单位官网、政府公告或高校就业网的原始链接。动态招聘系统只有在公开入口、robots 规则、字段结构和详情页都能稳定核验时才会启用；遇到登录、验证码或禁止自动访问的来源，系统会跳过并在来源状态中留下原因，而不是伪造岗位或绕过限制。
