# Phase 4：省级官方来源核验矩阵

## 目标

本阶段把“省份 × 五类官方来源角色”的扩源目标变成可审阅的证据台账。服务对象仍是中国石油大学（北京）地球科学学院：资源勘查工程本科生，以及地质学、地质工程、地质资源与地质工程硕士生和博士生。

本阶段不以岗位数量作为完成标准，而是逐条建立以下链路：

```text
省份/角色目标
  -> 正式 source_id 或候选 source_id
  -> 当前官方栏目
  -> 真实公告样例
  -> 发布日期、截止日期、招聘范围、附件等字段证据
  -> 备用官方入口
  -> 离线解析夹具
  -> 回归测试
  -> 运行时 robots/健康检查
```

第三方网站、教辅平台和公众号仍然只属于私有发现层；本阶段没有把它们写入学生端岗位数据。

## 当前批次

`data/source_validation_registry.json` 当前有 9 条记录：

| 省份 | 官方角色 | 来源 | 阶段 | 说明 |
| --- | --- | --- | --- | --- |
| 北京 | 人社/考试 | 北京市人力资源和社会保障局 | `adapter_fixture_verified` | 真实公告和 XLSX 岗位表 |
| 天津 | 自然资源 | 天津市规划和自然资源局 | `adapter_fixture_verified` | 事业单位招聘公告和 XLS 计划表 |
| 安徽 | 地质局/地质院 | 安徽省地质矿产勘查局 | `adapter_fixture_verified` | 高层次人才公告和 XLS 岗位表 |
| 山东 | 地质局/地质院 | 山东省地质矿产勘查开发局 | `adapter_fixture_verified` | 15 家所属单位公告和岗位汇总表 |
| 河南 | 地质局/地质院 | 河南省地质局 | `adapter_fixture_verified` | 事业单位联考公告和 XLSX 岗位表 |
| 山东 | 人社/考试 | 山东省人力资源和社会保障厅 | `adapter_fixture_verified` | 候选源，运行时仍停用 |
| 北京 | 自然资源 | 北京市规划和自然资源委员会 | `entry_checked_no_recruitment_sample` | 入口已查看，尚无可用样例 |
| 上海 | 自然资源 | 上海市规划和自然资源局 | `entry_checked_no_recruitment_sample` | 入口已查看，尚无可用样例 |
| 安徽 | 自然资源 | 安徽省自然资源厅 | `entry_checked_no_recruitment_sample` | 入口已查看，尚无可用样例 |

`adapter_fixture_verified` 只说明真实样例可以由现有解析器稳定解析。它不等于来源已启用，也不等于本轮存在地学岗位。山东人社考试来源即使具备夹具，也必须等部署环境完成合规 robots/TLS 健康检查后再决定是否启用。

## 真实官方样例

以下 URL 只作为管理员核验记录和测试依据，学生端仍只展示经过发布审计的岗位原文：

- [北京人社：北京工商大学 2026 年高层次人才及海外优秀学者招聘公告](https://rsj.beijing.gov.cn/xxgk/gkzp/202609/t20260917_4867574.html)
- [天津规划和自然资源局：所属事业单位 2026 年公开招聘公告](https://ghhzrzy.tj.gov.cn/zwgk_143/zfxxgk1/fdzdgknr1/zkly/202602/t20260228_7252855.html)
- [安徽省地矿局：安徽工业经济职业技术学院高层次人才招聘公告](https://dkj.ah.gov.cn/xwzx/tzgg/40788529.html)
- [山东省地矿局：所属事业单位 2026 年公开招聘人员公告](http://dkj.shandong.gov.cn/art/2026/7/17/art_356047_10333801.html)
- [河南省地质局：2026 年事业单位公开招聘联考公告](https://dzj.henan.gov.cn/2026/04-22/8190.html)
- [山东省人社厅：省属事业单位公开招聘公告](https://hrss.shandong.gov.cn/articles/ch00232/202601/f257f59a-0904-4130-935e-d959873b68d3.shtml)

## 管理查看

命令只读台账和本地运行数据库，不会因查看而触发网络采集：

```powershell
python -m job_hub.cli source-validation-matrix
python -m job_hub.cli source-validation-matrix --province 山东
python -m job_hub.cli source-validation-matrix --validation-stage adapter_fixture_verified
```

网页管理员接口：

```text
GET /api/admin/source-validation-matrix
Header: X-Admin-Token: <ADMIN_TOKEN>
```

支持 `province`、`role` 和 `validation_stage` 筛选。完整样例 URL、备用入口、夹具路径和运行状态只在管理员接口返回；公开 `/api/coverage` 只返回阶段计数、备份率和未完成数量等聚合信息。

## 不在本阶段宣称完成的内容

- 没有批量完成 31 省五类来源的自动化采集。
- 没有把入口可访问但未找到样例解释为“该省没有岗位”。
- 没有新增或虚构学生端公开岗位。
- 没有绕过 robots、登录、验证码、TLS 或访问控制。
- 没有把附件解析成功直接当作岗位真实性或学生报名资格；附件仍需后续逐岗位核验。

后续阶段应优先为已登记但未夹具验证的 verified 目标补充真实样例，再在部署环境逐站完成访问权限和受控试运行，最后才考虑启用新的 worker 来源。
