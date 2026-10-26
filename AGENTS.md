# 项目维护约束

本仓库提供 sing-box 与 Xray 两种面板配置，支持单独运行或共存。旧站点默认 sing-box；配置 `xray.instances` 后默认 Xray；Xray 独立安装设 `singbox.enabled=false`。切换来源只改变显示，不更改核心配置或实际链路。

- Xray 只读接入原生 StatsService，`reset=false`。独立账本保存基线、进程代次、日/分钟增量，不能重复累计、回填历史、混用另一个核心数据或分配虚构客户端字节。未知/缺失/陈旧值显示未知。
- 真实出口只取成功回显；探测上游须来自可证明的结构化独立路由，不按供应商品牌推断。原配置/认证/路由、Nginx 与其他服务由原运维工具管理。API 仅回环，不新增动态控制权限来弥补 UI 不支持的能力。
- Xray 独立模式不能读取 sing-box 配置、启动其采集器或执行其防火墙/封禁/探测任务；对应操作明确拒绝。旧 sing-box 版本的连接、封禁和审计兼容性必须保留。
- 媒体失败按已完成探测轮次和目标分别累计；重复评估/已读/重启不算新轮次。过期/未知不冒充恢复。公共大陆探测只能提交无令牌公开页面，核对 CN、运营商 ASN 与 TLS；禁止提交订阅令牌、认证、Cookie 或节点凭据，不能冒充完整订阅下载或 VMess 验证。
- 真实服务器地址、域名、SSH/代理凭据、UUID、导出与原始验收数据不得进入公开提交、fixture、构建或日志。私有参数放 Git 忽略目录且权限 0600；SSH 保留严格主机密钥检查。站点脚本与部署证据留在私有运维副本，通用部署说明见 `docs/xray.md`。
- 每次更新追加 `worklog.md` 并做与范围相称的验证。优先复用现有依赖；预计超过 1 GB 前检查磁盘，清理前取得用户明确确认。保留源码、交付、重要数据、备份与验收证据。

## 验证

```sh
cd dashboard/tests
XRAY_TEST_BINARY=/absolute/path/to/xray python -B -W ignore::ResourceWarning -m unittest test_panel test_media_probes test_xray test_xray_dashboard test_alert_sources test_engine_modes
cd ../web
node --test tests/engines.test.mjs
npm run build
```

原生测试必须有实际 Xray 二进制，在回环、临时目录和虚构配置下运行。不能针对生产跑自动部署测试。CI 固定官方 Xray 版本并验证 SHA256。修改核心兼容层时保持原生 wire/真实回环流量检查；页面更改需检查实际构建。
