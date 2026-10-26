# vps-dashboard · sing-box / Xray 双版本

代理服务器的浏览器监控面板，Flask + gunicorn、Vue 3 + ECharts、SQLite。支持 **sing-box 连接管理版**和 **Xray 节点监控版**，也可在同一面板切换两个核心的数据。界面切换不改变实际代理链路。

- `main` 保留 sing-box 版本；本 PR 从 `codex/xray-dual-version` 合入独立 `xray` 分支。合入后 `xray` 提供 Xray 监控与双内核切换，`main` 继续独立维护。
- 旧配置继续默认 sing-box；配置 `xray.instances` 后默认进入 Xray。仅使用 Xray 时设置 `singbox.enabled=false`，无需安装 sing-box。
- 示例域名/地址均为文档数据；实际凭据、节点配置、订阅及原始验收数据不入库。

## 两种版本

| 能力 | sing-box 连接管理版 | Xray 节点监控版 |
| --- | --- | --- |
| 数据接入 | 原生 API 连接事件 | 原生只读 StatsService，默认 2 秒轮询 |
| 流量 | 逐连接、来源 IP、目标、入/出站 | 按实例/入站统计；持久累计、今日、实时速率 |
| 拓扑/地图 | 客户端、规则、出口 | 配置中的入站/出站及实测出口；不虚构客户端位置 |
| 历史/每日 | 连接汇总、分钟/小时序列 | 独立节点账本、实例分钟/小时增量、自然日统计 |
| 在线来源 | 连接事件中的来源 | 取决于核心/用户标识支持，缺失显示未提供 |
| 断开/封禁 | 保留已有连接关闭与受限端口封禁、CDN 网关 | 当前只读，不提供逐连接关闭或 WS/CDN 封禁 |
| 出口/媒体 | Cloudflare 回显 + Instagram/YouTube/Facebook HTTPS | 对可证明独立路由的同一上游进行检测；不支持的路由明确留空 |
| 告警 | 保留原有条件告警 | 采集中断、逐网站连续失败、证书、磁盘、服务及可选大陆入口检查 |
| 账号/审计 | 共用登录、会话、CSRF 与审计 | 同左 |

两种统计口径独立。Xray 缺失/陈旧数据保留未知，不用 sing-box 的连接数或字节补齐。媒体 HTTPS 成功不代表视频播放、解锁或带宽测速；公开大陆检测不代表完整订阅下载。

## 配置与部署

- **已有 sing-box 面板**：沿用 [site.example.json](dashboard/deploy/site.example.json)、[原部署流程](docs/dashboard.md)。该流程需要已有 sing-box 及面板运行环境，会维护 sing-box API，不适用于 Xray 独立安装。
- **已有 Xray 服务器**：使用 [Xray 配置示例](dashboard/deploy/settings.xray.example.json)，按 [Xray 接入说明](docs/xray.md)启用回环 StatsService，再独立部署面板。支持多个 Xray 实例；保持既有配置、认证、路由和网站。
- **两者共存**：在既有面板 settings 中增加 `xray`，保留 `singbox.enabled=true`。登录后用“监控来源”切换。[共存边界](docs/existing-host.md)

后端依赖锁定在 `dashboard/deploy/requirements.lock`，服务器无需 Node。前端源码和构建资源均包含在仓库中。

## 开发与验证

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r dashboard/deploy/requirements.lock
cd dashboard/tests
XRAY_TEST_BINARY=/absolute/path/to/xray ../../.venv/bin/python -B -W ignore::ResourceWarning -m unittest test_panel test_media_probes test_xray test_xray_dashboard test_alert_sources test_engine_modes
cd ../web
npm ci
node --test tests/engines.test.mjs
npm run build
```

`XRAY_TEST_BINARY` 指向已校验的实际 Xray；原生测试使用虚构配置、临时目录和回环 HTTP/SOCKS/API，不连接生产服务器。CI 固定 Xray v26.3.27 并核对官方发布资产 SHA256。无原生核心时测试会明确失败。

原 sing-box 合成数据预览：`python dashboard/dev/devserver.py`，仅开发演示，不作为真实采集验收。

## 文档与安全

- [sing-box 面板与原有运维](docs/dashboard.md)
- [Xray 单独安装、接口与统计边界](docs/xray.md)
- [已有服务共存与回退](docs/existing-host.md)
- [原运维脚本](docs/operations.md)

管理 API 和面板仅监听回环，经实际域名 HTTPS 反代访问。登录只存盐和 PBKDF2 哈希；凭据、真实站点配置、`exports/`、`verification/` 与 `.env` 均不提交。公共大陆探测默认关闭，只允许无令牌公开页面，禁止提交订阅令牌、认证、Cookie 或节点凭据。

离线 IP 定位使用 ip2region（Apache-2.0）与 DB-IP Lite（CC BY 4.0），保留页面来源与精度标注。Xray 媒体探测仅对明确可表示的直连/单上游独立路由执行，不按供应商品牌推断。
