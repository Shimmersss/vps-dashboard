# sing-box 连接管理版

本文描述 sing-box 版本。Xray 独立配置、节点统计与功能边界见 [Xray 接入](xray.md)，双核心共存见 [共存部署](existing-host.md)。

面板挂在 Nginx 下的子路径（默认 `/monitor/`），域名和路径由 `dashboard/deploy/site.json` 决定。登录凭据和部署密钥只放在本地私有目录（`exports/`，不入库），服务器端只存 PBKDF2 哈希。支持多账户，会话可在面板中查看和撤销。

## 页面

- **概览**：实时速率（每秒）、今日和累计流量、在线客户端；实时吞吐（上下行或按出口堆叠）；24 小时、7 天、30 天流量趋势；出口卡片；热门目标与活跃客户端。
- **实时连接**：所有当前连接的来源、目标、入口、命中规则、出口、速率与时长，可单条断开。
- **链路拓扑**：桑基图（客户端 → 入口 → 分流规则 → 出口 → 出口公网地址），以及入口链路和出口链路详情。
- **地理分布**：客户端 → 服务器 → 各出口的连线地图，标注定位精度与来源。
- **客户端 / 连接历史 / 每日流量**：服务端分页、排序、搜索，可导出 CSV。点击 IP 查看 7 天流量、定位、热门目标和封禁状态。
- **网络质量**：网卡实时收发（`/proc/net/dev`）、网卡流量与代理载荷对比、链路质量趋势。链路质量每 60 秒用 `ss -ti` 读取 TCP 平滑 RTT 和区间重传，分四段：客户端 → 服务器（直连入口）、CDN 边缘 → 源站、服务器 → 上游代理、服务器 → 直连目标。UDP（QUIC）入站不在这项 TCP 统计内，但流量、连接和封禁照常统计。
- **出口节点**：各出站的出口地址、属地与 ASN、探测耗时曲线、出口地址变化记录。
- **访问控制**：封禁单个公网 IP 或网段（IPv4 ≥ /16，IPv6 ≥ /32），可设 1 小时到 30 天到期，到期自动解除并记入审计。
- **告警**：采集中断、出口连续探测失败、出口地址变化、证书将到期、磁盘使用、服务状态、单 IP 每日流量阈值。可选 Webhook 推送。
- **会话与审计**：登录、封禁、解除、断开连接、设置修改、手动探测都有记录。
- **系统与设置**：服务、证书、磁盘、账本、迁移、定位库版本、防火墙端口；可修改显示名称、入口类型、告警阈值、探测间隔（存入面板数据库，不改服务器配置文件）。

## 统计口径

- 计数是 sing-box 记录的代理有效载荷，不含 TLS/TCP 开销，也不是网卡计费流量（网卡流量在“网络质量”页单独显示）。
- 连接历史按 **来源 IP + 目标域名 + 协议 + 入站** 合并。
- 每日流量按 `site.timezone` 的日期统计。
- 采集时增量维护按 IP 和按合并键的汇总，以及分钟、小时两级序列；查询不扫描逐连接表。

## 定位

全部离线。国内地址以 ip2region 的省、市、运营商为准；境外地址使用 DB-IP Lite City / ASN。页面标注精度（城市、省级、国家）与数据来源。

## 出口探测

每 5 分钟（可调）经每个 direct / socks / http 出站请求 Cloudflare trace，记录出口地址、机房与耗时；同时检查 Instagram、YouTube 和 Facebook 的 HTTPS 可达性。媒体结果不代表视频播放、解锁或带宽测速。上游凭据只在内存中从 sing-box 配置读取，不写库，也不出现在任何接口里。

## 配置分层

面板代码不包含域名、服务器地址、端口或 sing-box 标签：

1. 内置默认：`dashboard/panel/settings.py`。
2. 站点配置：部署时由 `dashboard/deploy/site.json` 生成到服务器。
3. 密钥：服务器上的 0600 文件。
4. 面板内设置：存入数据库的 `prefs` 表。
5. 运行时发现：入站、出站、规则从 sing-box 配置读取（文件变化后自动生效）；防火墙端口取公网监听的入站，协议按入站类型识别。

迁移到新服务器：改 `.env` 的 SSH 目标；域名变了就改 `site.json` 的 `public_url`；然后重新部署。

## 封禁与真实 IP

- **直连入站**按 TCP/UDP 来源地址识别。规则只写在独立链（默认 `JOYSOL_NODE_BANS`），只从公网入站端口跳转进来，不影响 SSH 和面板。TCP 回 reset，UDP 直接丢弃。
- **CDN 入口**（回环 WebSocket 入站）：Nginx `auth_request` 网关先校验 CDN 回源时添加的密钥头，再检查封禁，然后把 CDN 追加的 X-Forwarded-For **最后一个** IP 交给 sing-box。不带校验头的直连握手返回 403，伪造的 XFF 无效。面板登录 IP 的识别方式相同。
- 部署脚本会给 sing-box 配置里所有回环 WebSocket 入站自动挂上网关。

## 开发与部署

- 后端 `dashboard/panel/`（入口 `dashboard/app.py`）；前端 `dashboard/web/`（Vite + Vue 3 + ECharts），构建产物在 `dashboard/static/app/`，服务器不需要 Node。
- 前端构建：`cd dashboard/web && npm run build`。
- 测试：`cd dashboard/tests && python3.10 -m unittest test_panel`，离线运行，不依赖防火墙或 sing-box。
- 本地预览：`python dashboard/dev/devserver.py`（合成数据）。
- 部署：`.venv/bin/python -B scripts/deploy-dashboard.py`，加 `--check-only` 只做只读预检。流程：
  1. 只读预检；
  2. 上传新版本到 releases 目录；
  3. 远端跑测试；
  4. 在内存中对生产账本演练迁移并核对汇总；
  5. 备份配置、单元、Nginx、sing-box 配置、代码和账本；
  6. 写入配置并 `nginx -t`；
  7. 切换版本，只重启面板；
  8. 健康检查，失败自动回滚。
- **瞬断影响**：每次部署重启面板约 5–15 秒，期间 CDN 入口的**新**连接无法通过网关校验；已有连接和直连入站不受影响。
- 服务：gunicorn gthread 1 worker × 16 线程，只监听回环；sing-box API 也只监听回环并使用 Bearer 验证。
- Nginx：子路径反代，SSE 接口关闭缓冲；接口响应 no-store，带哈希的静态资源 immutable 并预压缩。CSP 只允许同源。
- 验证脚本：`scripts/verify-dashboard.py`（登录、CSRF、封禁、网关）、`scripts/verify-esa-gate.py`（CDN WebSocket、源站绕过、请求头伪造）、`scripts/verify-dashboard-v2.py`（实时推送、出口探测、网络质量、资源压缩、定位来源）。

## 回退

- 版本间回退：把 `current` 链接指回上一个 release，然后重启面板服务。
- 停用面板前，先移除 WebSocket 入站的 `auth_request`，否则 CDN 新连接会被拒绝；封禁规则应先在面板里逐个解除。
