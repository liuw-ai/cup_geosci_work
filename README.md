# 地学就业信息站

面向中国石油大学（北京）地球科学学院及相邻专业学生的公开就业信息服务。网站面向手机和微信浏览器设计：学生无需登录即可查看当日就业日报、历史日报、全部在招岗位和每条岗位的官方原文链接。

系统把数据库作为唯一事实来源，而不是把在线文档当作数据库。每晚 20:00 将当天已核验的新增、更新和未来 7 天截止岗位固化为独立日报页面；/daily/latest 始终指向最新日报，适合固定在微信群公告中。

完整的数据流、专业匹配边界、100 人模拟检查和运维边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。生产部署、HTTPS 反向代理和手机/微信访问排查见 [DEPLOYMENT.md](DEPLOYMENT.md)。

项目进入长期迭代前的 Phase 0 审阅材料见 [docs/phase-0/README.md](docs/phase-0/README.md)。其中明确区分当前已经实现的能力、尚未实现的能力，以及后续扩源时不得突破的公开发布边界。

当前待审阅的 Phase 1 数据契约、SQLite 迁移、岗位证据、附件台账和私有线索状态机见 [docs/phase-1/README.md](docs/phase-1/README.md)。本阶段不新增爬虫或招聘数据；它为后续官方职位表解析和全国来源扩展建立可追溯的数据基础。

## 已实现的能力

- 官方来源白名单：单位官网、官方招聘系统、政府公开招聘平台、高校就业网优先。
- 合规采集：只访问登记来源，检查 robots.txt，不处理登录、验证码或绕过反爬限制。
- 可靠性分级：A 级为单位/政府官方来源，B 级为高校或经认证就业信息；第三方转载不直接公开。
- 当前已自动接入的校外来源：**中国科学院人才招聘网、Halliburton 官方招聘页、SLB 官方职位页、中国地质调查局招聘公告页、自然资源部官网及所属企事业单位公开招聘平台、中国煤炭地质总局、中国冶金地质总局、紫金矿业社会/校园招聘门户**；中国石油大学（北京）就业网仅是其中一个 B 级来源，不是系统唯一来源。
- 岗位标准化：保留单位、岗位、原始地点、标准化省份/城市/国家地区、学历、专业标签、发布时间、截止日期、原始链接和报名链接；国际岗位使用官方地点中的国家代码，不从单位名称猜测省份。
- 单位体系：用可审计的单位注册表区分油气上游业主、集团内技术服务、独立油服和国际油服；保留公告原始单位名，不对模糊名称强行归类。
- 地学匹配：围绕资源勘查工程、地质工程、地质学、地质资源与地质工程，并识别石油地质、测井、储层、地球物理、地学数据、遥感、GIS 等相邻方向。
- 学生画像筛选：提供 7 个不保存个人数据的“学历 × 专业”画像，分别覆盖资源勘查工程本科，以及地质学、地质工程、地质资源与地质工程的硕士和博士；页面明确区分“明确匹配”和“需核验原公告”。
- 队列验证：内置固定的匿名 100 人地球科学学院模拟队列，利用当前真实岗位库输出各培养方向的明确匹配、待核验、覆盖类别和未覆盖情况。
- 岗位分类：油气上游业主与研究机构、油气工程技术服务、管网/炼化/综合能源、自然资源/地调/地勘、矿产资源与矿业、地质工程/环境/基础设施、科研院所/高校/博士后、事业单位与人才引进、公务员与选调、金融与央国企综合机会、能源与地学拓展。
- 去重与更新记录：同一官方公告只保留一条岗位记录，内容变化会进入“信息更新”日报。
- 邮件通知：日报完成后可通过 SMTP 向管理员发送当天链接；采集失败也会触发异常提醒。
- 发布前审计：日报会检查岗位来源域名、相关度阈值、招聘会/采购噪声、来源启停状态和过期状态；存在数据完整性问题时拒绝固化日报。
- 运行健康：worker 会把最近心跳写入 SQLite；Docker Compose 分别检查 Web 接口和 worker 心跳，便于发现定时任务停止。
- 来源健康：入口可访问性与抓取运行结果分开记录；最近一次 `crawl_run` 单独保存候选数和开放匹配数，栏目退化、访问受限和网络/解析异常绝不能显示成“无岗位”。
- 招聘流程过滤：标题级规则统一排除进入面试、面试名单、递补、资格审查、成绩、体检、考察、拟聘和拟录用等后续流程通知；它们不是新的可投岗位。
- 扩源矩阵：`data/source_targets.json` 按 31 个省和五类官方角色记录已核验、候选和待定位入口。候选入口只进入扩源排期，不会被 worker 抓取或在学生端显示。
- 质量报告与日报快照：`/api/coverage` 输出来源健康、每省有效/备用来源、来源角色矩阵、原文/专业/学历/地点/截止日完整率、来源与类别集中度，以及 100 人模拟中的明确匹配率；每次同步会记录当天可更新的质量快照，用于与前一个不同日期比较，而不是只看岗位总数。
- 公开接口：/api/jobs 提供只读 JSON 数据，支持 `province` 省份筛选；/api/coverage 输出上述可观测指标。
- 私有线索池：中公、华图、国聘、行业公众号等只可进入受保护的候选线索池，完成官方原文核验后才可发布为公开岗位。

## 项目结构

~~~text
job_hub/
  app.py          公开网站与管理接口
  db.py           SQLite 数据库与查询
  contracts.py    来源、单位、证据、附件和线索状态的数据契约
  sources.py      公开来源采集器和 robots 合规检查
  locations.py    省份、城市和国家/地区标准化
  source_targets.py 31 省五类官方来源扩展矩阵
  coverage.py     省份来源覆盖、字段完整率和集中度检查
  matching.py     专业匹配、分类和日期提取
  profiles.py     地球科学学院学历×专业画像与可解释匹配
  simulation.py   匿名 100 人覆盖模拟
  pipeline.py     采集、去重、岗位写入
  reports.py      日报快照
  worker.py       定时同步、20:00 发布和邮件通知
data/sources.json 官方来源白名单和已验证采集器
data/provincial_sources.json 31 省官方入口矩阵（默认待核验）
data/source_targets.json 31 省五类来源角色核验目标（不自动抓取候选项）
data/employer_registry.json 可审计的高价值单位标准名与体系关系
examples/         人工补录模板
tests/            自动化测试
ARCHITECTURE.md    数据流、收录边界、运维与代码职责
~~~

## 本地启动

项目在 Python 3.11 环境中开发。先创建隔离环境并安装依赖：

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
~~~

本地调试时，.env 中的 APP_DATA_DIR 和 APP_DATABASE_PATH 可以改为项目内的 runtime 路径。不要把真实 SMTP 授权码、管理员令牌或服务器密码提交到 Git。

初始化来源和数据库：

~~~powershell
python -m job_hub.cli init
~~~

启动公开网站：

~~~powershell
python -m flask --app job_hub.app:create_app run --host 127.0.0.1 --port 8080
~~~

浏览 http://127.0.0.1:8080。在本地测试时，首次没有岗位数据会显示清晰的空状态；正式岗位只应通过已核验的官方来源或管理员补录进入系统。

立即执行一次来源同步：

~~~powershell
python -m job_hub.cli sync
~~~

先执行数据审计。审计通过后，再生成当日日报：

~~~powershell
python -m job_hub.cli audit
python -m job_hub.cli publish
~~~

如果 SMTP 已配置并希望在手动发布时发送邮件：

~~~powershell
python -m job_hub.cli publish --send-email
~~~

单独重试一个已启用来源，避免为排查某一官网而重复访问所有来源：

~~~powershell
python -m job_hub.cli sync-source halliburton-career
~~~

如发现某个来源的旧数据存在误采，先预览受影响数量；确认后只删除该来源的岗位及其变更事件，来源配置和其他来源数据不会被删除：

~~~powershell
python -m job_hub.cli purge-source cupb-career
python -m job_hub.cli purge-source cupb-career --confirm
~~~

以当前数据库中的真实岗位进行 100 人匿名覆盖检查。该命令只读取岗位库，绝不创建学生账号、姓名、联系方式或其他个人数据：

~~~powershell
python -m job_hub.cli simulate-cohort
python -m job_hub.cli simulate-cohort --output .\runtime\cohort-coverage.json
~~~

查看“岗位总数以外”的质量指标。输出会显示每省已登记、入口可访问和最近一次成功扫描来源数，备用入口、五类来源角色核验矩阵、地点/截止日质量、来源/类别集中风险、成功扫描但无开放匹配的来源，以及 100 人匿名队列中明确匹配与需核验数量：

~~~powershell
python -m job_hub.cli coverage
python -m job_hub.cli coverage --output .\runtime\coverage.json
python -m job_hub.cli coverage --record --output .\runtime\coverage.json
~~~

`sync` 和常驻 worker 会在同步后自动记录当天的质量快照；`coverage --record` 用于人工复核后的补记。同一天的快照可以被后一次同步替换，趋势比较只取前一个不同日期，避免同日重复执行制造虚假的“增长”。

### “本轮未发现匹配岗位”的严格含义

系统只有在一个省的人社/考试、自然资源、地质局/地质院、事业单位统一招聘、公务员五类官方角色均已核验并绑定来源，且全部已启用本地来源与上述五类来源均有健康的最近一次成功扫描、开放匹配数均为零时，才会写“本轮未发现匹配岗位”。来源未定位、访问受限、页面路径变更、robots/TLS 未核验、解析异常或尚未扫描时，页面和报告只能说明覆盖尚未完成，绝不能推断该省没有招聘。

轻量检查一个公开来源时，先检查 robots.txt，再只请求一次公开入口，不抓取岗位。默认只检查启用来源；检查停用来源必须显式指定，避免对 31 省待核验入口做无意的批量访问：

~~~powershell
python -m job_hub.cli source-health --source-id mnr-public-recruitment
python -m job_hub.cli source-health --include-disabled
~~~

## 每日自动运行

生产环境运行独立 worker：

~~~powershell
python -m job_hub.worker
~~~

worker 启动时会同步来源，随后按 SOURCE_SYNC_INTERVAL_MINUTES 定期刷新。到达 DAILY_PUBLISH_TIME=20:00 后，它会执行一次最终同步、运行数据审计、冻结当天日报并发送邮件。进程重启或服务器短暂故障后，如果当天 20:00 已过且日报不存在，worker 会自动补发当天日报。若审计发现原始链接域名不在白名单、相关度低于阈值、混入招聘会/采购噪声或岗位状态异常，worker 不会发布日报，并向管理员发送异常邮件。

worker 每 30 秒更新一次心跳。若进程在单个来源采集期间异常退出，下一次同步只会将超过 `CRAWL_RUN_STALE_SECONDS`（默认 1800 秒）的旧 `running` 记录标为 `interrupted`，不会删除记录或把它误记为成功；发布前审计会阻止仍超时的启用来源。可在服务器上检查 worker 状态：

~~~bash
docker compose exec worker python -m job_hub.cli worker-health --max-age 180
~~~

## 邮件提醒配置

推荐使用专门的管理员邮箱和 SMTP 授权码，而不是邮箱登录密码。填写 .env：

~~~dotenv
MAIL_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=jobs@example.com
SMTP_PASSWORD=邮箱服务商生成的授权码
SMTP_FROM=jobs@example.com
SMTP_TO=你的接收邮箱@example.com
SMTP_USE_SSL=true
~~~

短信通常按条收费，并且涉及签名、模板和服务商审核，因此不作为每日成功提醒的默认渠道。企业微信可在以后接入为另一种通知器；当前邮件方案不依赖企业微信账号。

## 服务器部署

推荐在 Linux 服务器使用 Docker Compose。服务器需要能够访问公开招聘页面、拥有持久化磁盘，并通过 HTTPS 对学生开放网页。

~~~bash
cp .env.example .env
# 编辑 .env，至少设置 APP_SECRET_KEY、ADMIN_TOKEN、APP_BASE_URL 和邮件配置
docker compose config
docker compose up -d --build
docker compose logs -f worker
~~~

Compose 默认只把 8080 绑定到服务器本机的 `127.0.0.1`，不直接暴露给公网。请在 Nginx、Caddy 或学校现有网关上配置 HTTPS 反向代理到 `127.0.0.1:8080`。APP_BASE_URL 必须填写学生实际访问的 HTTPS 域名，例如 https://jobs.example.edu.cn，否则邮件中的日报链接会不正确。`docker compose ps` 应显示 web 与 worker 均为 healthy；worker 健康检查读取共享 SQLite 中最近 180 秒的心跳。

若服务器位于中国大陆且网站向公众开放，通常需要准备已备案域名；若使用境外服务器，不需要 ICP 备案，但应评估中国大陆和微信内访问速度。网页可被微信直接打开，不要求学生安装飞书、腾讯文档或其他办公软件。

## 来源维护与扩展

来源白名单在 data/sources.json。其中启用的来源会被 worker 定期尝试采集；未启用来源是已登记但需要先核验当前招聘入口、页面结构或访问规则的来源。不要把“已登记”误解为“可以不经检查地高频爬取”。

### 当前自动采集范围

| 来源 | 自动化方式 | 当前原则 |
| --- | --- | --- |
| 中国石油大学（北京）就业信息网 | 专用岗位页适配器 | 仅保留具体岗位和招聘公告；招聘会、宣讲会、补贴和就业通知不会发布。 |
| 中国科学院人才招聘网 | 官方岗位列表 + 详情页适配器 | 用岗位名称、学科领域和专业要求筛选，不能因“研究所名称”含地学词就误收财务、行政等岗位。 |
| Halliburton Careers | 官方公开检索页适配器 | 只收录实际岗位标题/结果行中明确涉及地学、油气、测井、储层、钻完井等方向的机会；可包含符合条件的海外岗位。 |
| 自然资源部所属企事业单位公开招聘平台 | 平台公开招聘 API 适配器 | 读取网站公开前端所使用的只读公告和职位字段；按岗位本身的专业、学历、地点和截止日期过滤，不因单位名称带“地质”误收财务或行政岗。 |
| 中国地质调查局、自然资源部 | 官方公告页适配器 | 只读取明确招聘、人才引进、博士后、科研助理或实习公告；采购、招标、中标、征求意见等被排除。 |
| SLB | 官网公开 Coveo 检索适配器 | 运行时读取网页公开检索配置，逐条读取官方详情；不保存令牌、不登录、不调用申请接口，搜索地点与详情地点不一致时拒绝发布。 |
| 已验证的省级自然资源、地质局/院公告栏目 | 严格公告页适配器 | 已启用北京、天津、上海、江苏、浙江、安徽、福建、湖北自然资源栏目，以及山东、湖南、宁夏、安徽、河南、河北地质局/院栏目；只保留招聘/招录/人才引进/博士后等原始公告，并按开放期与专业证据过滤。 |

中国石油大学（北京）公开就业网页面实际提供“学院发布、招聘公告、全职岗位、实习岗位、宣讲会、双选会”等入口。适配器仅从前三类中的正式招聘内容取数；对于包含岗位表格的公告，优先提取岗位、专业范围、学历/面向对象和工作地点，避免因单位简介中的行业词造成误判。

### 已登记但暂不自动抓取的重点官方渠道

中国石油、中国石化、国家管网、Baker Hughes、Weatherford、中国石油大学（华东）智慧就业网、国家大学生就业服务平台、国考专题、部省人社系统和中国地质科学院等入口仍保留在白名单中，因 robots 规则、浏览器验证、动态检索、详情登录限制、年度专题变化或当前网络连接不稳定而暂不自动抓取。中国海油入口已核验为 `https://cnooc.zhaopin.com/`，当前年度岗位页显示暂无数据，仍保持停用，待招聘周期内重新核验公开岗位结构后再启用。系统不会绕过登录、验证码、反爬或访问限制；待找到允许公开读取的稳定接口或专用页面结构后，再单独开发适配器并在本地人工审核结果后启用。

31 个省级自然资源入口登记在 `data/provincial_sources.json`。除上述已启用来源外，甘肃省地矿局的人事栏目已经定位到正式公开页面，但其 `robots.txt` 会从 HTTP 跳转到 HTTPS 后发生 TLS 握手失败；因此保留为 `robots_tls_unverified` 候选来源并停用，不会由 worker 自动访问。其他入口仍可能处于路径变更、访问受限或当前网络异常的待核验状态。`data/source_targets.json` 进一步把每省的人社/考试、自然资源、地质局/地质院、事业单位统一招聘、公务员五类目标拆开，只有绑定真实 `source_id` 的 `verified` 项才计入已核验来源。登记不等于可抓取，也不等于当地没有招聘。

新增来源前应逐项确认：

1. 该页面是招聘单位、政府部门或高校的正式公开页面。
2. robots.txt 和站点条款允许所需频率的公开访问。
3. 页面无需登录、验证码、账号或付费权限。
4. 页面结构可以稳定提取标题、原始链接和发布日期；动态站点应添加专门的官方 API 或页面适配器，不应通过绕过限制处理。
5. 先在本地执行一次同步，人工查看候选结果、重复率和误判情况，再将来源启用到生产环境。

对中国石油、中国石化、中国海油三家上游业主，要与其体系内技术服务乙方分开标注：例如东方物探、长城钻探、川庆钻探、渤海钻探、石化石油工程技术服务、中海油服、中海油能源发展属于集团体系内的技术服务/工程单位；杰瑞、安东、中曼、海隆、贝肯、通源属于独立或民营油服；SLB、Halliburton、Baker Hughes、Weatherford 属于国际油服。国家管网、自然资源与地勘系统、事业单位、考公、科研院所、银行和在华外企则按各自官方入口单独接入。通用页面采集器仅适合结构明确的公开公告页，不会假装能够可靠处理每一个 JavaScript 招聘系统。

## 人工补录已核验岗位

当正式公告无法自动采集时，可以使用 examples/verified-job.template.json 填写岗位，并确保 source_url 是单位官网、政府公告或高校就业网原始链接：

~~~powershell
python -m job_hub.cli import-json .\examples\verified-job.template.json
~~~

也可以使用受保护接口：

~~~text
POST /api/admin/jobs
Header: X-Admin-Token: <ADMIN_TOKEN>
Content-Type: application/json
~~~

这条接口仅用于管理员补录，默认不会在页面中暴露令牌。

### 内部候选线索

第三方平台和行业公众号可作为“发现线索”，但不能直接导入岗位库。管理员可通过 `POST /api/admin/leads` 或 `python -m job_hub.cli import-leads-json` 保存其链接；候选线索不出现在网页、日报或 `/api/jobs`。只有写入 `official_url`、核验说明，并将状态更新为 `official_content_verified` 后，管理员才可调用 `POST /api/admin/leads/<id>/publish` 发布对应的单位官网、政府官网或高校官网原文。

## 验证

~~~powershell
python -m pytest -q
~~~

测试覆盖中英文专业匹配、地点和单位体系标准化、v0.2 到 v0.3 的 SQLite 加法迁移、岗位去重、派生标签刷新、来源健康、31 省矩阵、自然资源部公开职位接口、候选线索私有性、来源覆盖指标、北京时间日报边界、来源审计、日报生成、公开页面和管理员令牌校验。

## Git 版本管理

项目根目录使用 Git 管理源代码、来源配置、容器定义、测试和文档；`.env`、SQLite 数据库、日志、缓存与模拟输出均被忽略。每次调整来源规则或部署配置前后，请检查并提交清晰的变更记录：

~~~bash
git status
git add job_hub data tests Dockerfile docker-compose.yml README.md ARCHITECTURE.md
git commit -m "说明本次变更"
~~~

将远程仓库接入学校或团队平台前，先确认 `.env`、runtime 数据库和 SMTP 授权码没有被暂存。
