# Phase 7：公开动态校招接口适配器

## 目标

在 Phase 6 国家能源入口矩阵基础上，先处理一个有公开页面、公开前端脚本和只读岗位接口的真实入口：中国海油 2026 届校园招聘页面。适配器只读取公开页面和页面自身调用的岗位检索接口，不登录、不提交申请、不绕过验证码或访问控制。

## 交付内容

- `job_hub/sources.py`：新增 `zhaopin_campus` 适配器。
- `data/sources.json`：登记中国海油公开校招接口参数，但保持 `enabled: false`。
- `data/national_source_probes.json`：保存 2026-09-22 的真实入口/API探测证据。
- `job_hub/national_probes.py` 与契约：校验和汇总探测证据。
- 管理员接口：`GET /api/admin/national-source-probes`。
- CLI：`python -m job_hub.cli national-source-probes`。
- 回归测试：公开空结果、业务错误、专业过滤、官方链接白名单和证据私有性。

## 真实探测结论

| 项目 | 结果 |
| --- | --- |
| 官方入口 | `https://cnooc.zhaopin.com/job/index.html`，HTTP 200 |
| 页面证据 | 页面标题为中国海洋石油集团有限公司 2026 届校园招聘，并加载公开前端脚本 |
| 只读接口 | `https://fe.zhaopin.com/grace/api/dsc/search-job-list` |
| 接口 HTTP 状态 | 200 |
| 接口业务状态 | `code=500`，消息为“接口转换失败” |
| 当前发布岗位 | 0 |
| 来源运行状态 | 停用，不能解释为无岗位 |

HTTP 200 只代表接口响应到达，不代表岗位查询成功。只有 `code=200`、`data.jobList` 和 `data.pageInfo` 同时满足契约，才允许产生岗位；业务错误、字段缺失和不一致分页信息都会使本次采集失败并记录故障。

## 适配器规则

1. 先请求公开校招页面并确认页面/脚本可访问。
2. 使用页面公开的 `companyId`、`scene` 和岗位查询参数调用只读接口。
3. 只接受官方白名单域名的岗位原文链接。
4. 岗位必须有标题、详情文本和地学专业证据；财务、行政、人力等非目标岗位被过滤。
5. `jobList=[]` 只有在接口明确返回 `totalNum=0` 时才是成功无匹配。
6. 业务错误和适配器异常进入失败记录，不会生成空日报结论。

## 运维查看

```powershell
python -m job_hub.cli national-source-probes
python -m job_hub.cli national-source-matrix --affiliation 中国海油体系
```

探测细节和接口地址仅在管理员 API 中返回；学生端不会看到内部 API、脚本路径或失败堆栈。

## 阶段边界

- 没有启用中国海油来源，因此本阶段没有新增公开岗位。
- 没有把智联招聘聚合首页或第三方转载作为学生端证据。
- 没有使用 `curl_cffi`、浏览器指纹伪装或绕过限制手段。
- 该适配器目前是“夹具验证通过、线上接口探测失败”，必须在接口恢复后重新人工核验，再改为启用。

下一阶段应在服务器环境重新执行该探测，并并行处理中国石油、中国石化、国家管网的正式公告/职位表或公开接口；任何一个入口都要先完成相同的字段和回归验证。
