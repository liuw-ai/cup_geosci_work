# Phase 34 质量报告

日期：2026-09-25  
分支：`phase/34-ccgc-current-source`

## 官方入口核验

- 官方首页：`https://www.ccgc.cn/`
- 人才招聘栏目：`https://www.ccgc.cn/?mod=zhaopin&tid=129`
- 官方分页：17 页，详情链接形如 `/?mod=zhaopin_detail&id=240289`。
- 旧入口 `https://www.ccgc.cn/rlzy/rczp.htm`：HTTP 404，已从采集配置移除。

## 发布门禁

栏目迁移只修复来源可达性，不直接增加学生端岗位。详情页仍需包含招聘正文并通过地学专业相关度、截止日期/开放期和官方域名校验。

## 自动化验证

```text
python -m pytest -q
257 passed
```
