# BGP 官方来源证据

## 来源身份

| 项目 | 记录 |
| --- | --- |
| 单位 | 中国石油集团东方地球物理勘探有限责任公司 |
| 中国石油层级 | 中国石油天然气集团有限公司 -> 集团内油气工程技术服务单位 |
| 官方域名 | `bgp.com.cn` |
| 招聘原文 | <https://www.bgp.com.cn/bgpen/Recruitment/first_common2023hr.shtml> |
| 备用官方入口 | <https://www.bgp.com.cn/> |
| 核验日期 | 2026-09-22（Asia/Shanghai） |
| 传输模式 | `direct`，忽略本机代理环境变量；仍保留 TLS 校验 |
| robots | `https://www.bgp.com.cn/robots.txt` 返回 HTTP 404 |
| 页面状态 | HTTP 200，最终域名仍为 `www.bgp.com.cn` |

## 岗位字段

页面原文中可读取以下字段：

- Job Title：`Seismic Data Processing Geophysicist (Experienced)`
- Quantity：`One`
- Work Location：`Saudi Aramco GDAD Office, Saudi Arabia`
- Job Type：`Full-time, On-site Project Position`
- Job Requirements：本科及以上；Geophysics、Exploration Geophysics、Petroleum Geology 或相关地学专业；硕士优先。
- How to Apply：通过页面公布的 BGP 招聘邮箱投递简历。
- Date：`2026/6/27`

页面没有固定截止日期，也没有独立的超链接报名地址。因此数据库中的 `deadline_date` 和 `application_url` 保持为空，不能用推断值冒充原文字段；岗位是否暂时展示由来源的 120 天无截止日期窗口控制。

真实页面的最小摘录保存在 [`tests/fixtures/national_energy/bgp_recruitment_2026.html`](../../tests/fixtures/national_energy/bgp_recruitment_2026.html)，只用于离线回归，不替代线上官方原文。
