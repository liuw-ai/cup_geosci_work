# Phase 26：服务器来源诊断与适配前置契约

本阶段不伪造“已经在云服务器直连成功”，而是把服务器上线后的第一步做成可重复、可审计的来源诊断。它解决的是：本机代理/TLS 问题、服务器出口问题、目标站点 403/412、robots 限制和栏目路径失效不能再混为一个“采集失败”。

## 诊断命令

在服务器项目目录执行：

```bash
export HTTP_TRANSPORT_MODE=direct
export HTTP_CLIENT=curl_cffi
python -m job_hub.cli source-health \
  --include-disabled \
  --output runtime/source-health-server.json
```

如果服务器没有安装 `curl-cffi`，先使用 `HTTP_CLIENT=requests` 完成基线，再安装可选依赖复测。不要关闭 TLS 校验，也不要使用代理池、验证码绕过或未授权 Cookie。

输出中的每个来源包含：

- `transport`：environment/direct、代理环境是否存在、HTTP 客户端；
- `robots`：不存在、允许、禁止、HTTP 错误或无法核验；
- `entry`：入口状态码、最终 URL、是否发生栏目路径丢失；
- `detail`：面向管理员的失败原因，不代表岗位数量。

## 状态解释

```text
source_active       robots 允许且登记入口可访问，不代表有匹配岗位
source_blocked      robots、403、412 或访问策略阻断
source_degraded     入口失效、软 404、路径变化或 robots 无法核验
source_error        网络/TLS/连接错误
```

只有 `source_active` 才进入后续适配器试运行；其余状态保留为故障或待定位，不显示为“无岗位”。

## 后续适配顺序

1. 服务器诊断确认出口和目标状态；
2. 对中石化、中石油、国家管网、中海油分别建立列表、分页、详情、附件适配器；
3. 动态 SPA 使用公开只读浏览器捕获或公开接口，保存岗位级证据；
4. 政府职位表进入 `source_artifacts`、解析和人工复核队列；
5. 只有岗位级专业、学历、地点、截止日期证据完整且明确匹配，才进入学生端。

本阶段诊断文件属于管理员运行产物，不会被学生端读取，也不增加岗位数量。
