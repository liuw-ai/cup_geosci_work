# Phase 85 国聘失败详情定向重试

国聘服务器实测出现过“8 页列表已完成、146 个详情中 129 个成功、17 个失败”的状态。重新扫描 146 个列表卡片既浪费请求，又可能因下一次页面波动丢失对同一批失败详情的诊断。本阶段将一次分页完整的 partial 视为冻结扫描：只重试其 `failure_records` 中的官方详情 URL。

重试输入必须同时满足：状态为 `partial`、分页完成、已成功行数与详情成功数一致、失败记录数与失败详情数一致、每个失败项有允许访问的官方详情 URL、且捕获年龄不超过 12 小时。缺少任何条件时不把它补成成功，也不把它解释为无岗位。

每个重试详情仍由同一岗位级字段门禁处理：岗位、专业范围、学历要求、地点、人数、截止日期和官方详情链接缺一不可。详情页未显示招聘单位时，仅可回退到同一官方列表卡片中已记录的单位名称。标题、单位或专业都不会从搜索结果、公告总述或第三方页面推断。

## 自动运行规则

`cmgb-browser` worker 每轮先检查最新的 `captures/cmgb-iguopin-browser.failure.json`：

1. 若它是 12 小时内、分页完整的 partial，仅读取失败详情；
2. 所有目标成功，才生成新的完整 success-only 捕获；
3. 仍有一个失败，则写入新的失败归档并结束本轮，不进行全量列表扫描；
4. 归档过期、不是 partial，或不具备定向重试条件时，才回到普通全量扫描。

因此“访问故障”“部分详情失败”“全量成功”“扫描无匹配”仍是不同状态，学生端来源不会把前三者伪装成岗位变动。

## 服务器升级后的受控操作

服务器当前旧版可能在正式路径保留了 partial 文件。升级本阶段后，先在停用动态来源的前提下检查该文件的状态；只有确实为旧 partial 才执行一次以下命令：

```bash
docker compose -f docker-compose.browser.yml exec -T cmgb-browser \
  python -m job_hub.cli cmgb-quarantine-partial --confirm
```

它先创建失败归档，再将旧文件移动为同目录的 `*.legacy-partial.json`，不删除任何文件。随后可手工执行一次定向重试：

```bash
docker compose -f docker-compose.browser.yml exec -T cmgb-browser \
  python -m job_hub.cli cmgb-detail-retry \
  --failure-capture captures/cmgb-iguopin-browser.failure.json
```

`ok: true` 只说明命令运行；只有输出 `status: success` 且随后通过既有 `cmgb-production-transition` 预检，才可考虑动态来源接管已审阅快照。当前阶段不启用该来源，不修改学生端岗位，也不自动部署到服务器。
