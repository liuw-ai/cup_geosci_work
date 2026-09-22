# Phase 9：传输诊断与合规采集梯子

## 目标

本阶段解决一个容易误判、但会直接影响岗位覆盖率的问题：本地代理握手失败、目标站点访问策略限制、页面动态壳和真正的“扫描成功无匹配”必须被记录成不同状态。系统新增显式的 HTTP 出站模式，并把模式写入入口探测证据，使管理员可以在服务器上复测，而不会把本机网络故障写成“没有岗位”。

本阶段不承诺通过 403、412、验证码或 robots 限制，也不新增虚构岗位。它为后续中国石油、中国石化、国家管网和中国海油的逐站适配提供可回退的诊断基础。

## 交付内容

- `job_hub/transport.py`：`environment` 和 `direct` 两种显式传输模式；统一配置新建或注入的 `requests.Session`。
- `job_hub/config.py`、`.env.example`：新增 `HTTP_TRANSPORT_MODE`，默认 `environment`。
- `job_hub/entry_probes.py`：入口探测记录传输模式和代理环境是否存在；继续执行 robots、跳转、动态壳和访问策略分类。
- `job_hub/sources.py`、`job_hub/attachments.py`：正式来源采集和官方附件处理沿用同一传输策略。
- `job_hub/cli.py`：`national-entry-probe --transport direct|environment`，输出完整传输上下文。
- `data/national_entry_probe_runs_direct_diagnostic.json`：2026-09-22 本机直连只读诊断证据。
- `tests/test_transport.py`、`tests/test_entry_probes.py`：模式、大小写代理变量、诊断证据和入口回归测试。

## 传输模式

| 模式 | 行为 | 适用场景 | 不能解决的问题 |
| --- | --- | --- | --- |
| `environment` | 遵循 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 等环境变量 | 本地开发或明确需要代理的网络 | 代理 TLS EOF、目标站点 403/412 |
| `direct` | 对该 Session 设置 `trust_env=False`，忽略代理环境变量并使用直连出口 | 服务器有合规、稳定的直接公网出口；诊断本机代理 | robots 禁止、WAF/访问策略、登录/验证码、站点未公开接口 |

两种模式都保留 TLS 证书校验，不使用浏览器指纹伪装、代理轮换、验证码服务或其他访问控制规避手段。`proxy_environment_present` 只记录是否检测到代理变量，不记录变量值或凭据。

## 采集梯子

后续每个官方来源按以下顺序推进，上一层失败时保留证据并停止自动采集，不把失败当作空结果：

1. 在 `environment` 和服务器 `direct` 模式分别执行轻量 robots/入口探测，确认是本地出口问题还是目标策略问题。
2. 入口允许时，接入公开 HTML、公开 JSON/ATS 或公告列表，先完成离线夹具和字段契约。
3. 从官方公告页登记 PDF/Excel/扫描件，使用受控下载、哈希、文本/表格提取和人工复核。
4. 动态页面只有在公开页面和公开只读数据行为可以稳定复现、且符合站点规则时才开发专用适配器。
5. 公众号、中公、华图、国聘等只进入私有发现层；必须回溯到单位、政府或高校正式原文后才能发布。
6. 公开入口不可访问但存在正式公告时，使用管理员人工核验导入，保留原文 URL、公告和附件证据；不以第三方转载替代原文。

## 真实直连诊断（2026-09-22）

| 体系 | 直连观察 | 结论 |
| --- | --- | --- |
| 中国石油 | 招聘平台和集团官网入口返回 HTTP 412 | 访问策略受限，不能判定无岗位 |
| 中国石化 | 招聘平台 robots 返回 403，集团官网 robots 请求超时 | 访问受限/来源故障，不能判定无岗位 |
| 中国海油 | 校招页 HTTP 200 但为动态壳；集团官网可达并发现公告栏目 | 需要公开数据或公告专用适配器 |
| 国家管网 | 招聘子域 robots 返回 403；集团官网可达并发现 `https://zhaopin.pipechina.com.cn/recruit` | 招聘子域受限，官网入口可用于人工定位 |

该记录保存在 `data/national_entry_probe_runs_direct_diagnostic.json`，不是岗位数据，也不会进入学生端。

## 运维命令

本地代理环境诊断：

```powershell
python -m job_hub.cli national-entry-probe --transport environment --environment local-network --output .\runtime\national-entry-probe-environment.json
```

服务器直连诊断：

```bash
HTTP_TRANSPORT_MODE=direct \
python -m job_hub.cli national-entry-probe \
  --transport direct --environment production-server \
  --output /var/lib/job-hub/national-entry-probe-direct.json
```

直连结果仍需管理员审阅。只有在官方页面、robots、字段、详情页、附件和回归测试全部通过后，才可把来源从停用改为启用。

## 阶段边界

- 本阶段不改变 SQLite schema，不改变学生端页面，不新增岗位。
- 四大能源体系仍不能宣称已完成大规模国内岗位采集。
- `accessible_html` 只表示页面层可读；`accessible_dynamic_shell` 只表示动态壳可读；两者都不是岗位。
- `access_policy_block`、`robots_blocked`、`source_unavailable` 永远不能转化为“扫描成功无匹配”。

## 下一阶段入口

优先在长期在线服务器执行同一探测并保存运行证据，然后按“国家管网官网公告/职位表、中国海油公告栏目、中国石油和中国石化下属油田/研究院/工程技术单位”顺序逐站建立专用适配器。验收依据是字段完整率、官方证据完整率、来源故障率和明确专业匹配数量，而不是入口登记数。
