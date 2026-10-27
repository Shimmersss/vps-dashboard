# Xray 接入

## 功能范围

Xray 版面板包括这些页面：
- 概览、节点列表、实时速率、配置拓扑；
- 用户流量与在线地址、实测出口地图、流量记录、自然日流量（节点 / 用户 / 出站）；
- 出口探测与出站流量、网卡和系统状态（含核心运行时长、内存、协程数）；
- 可选的访问控制（封禁公网入站端口的来源地址）。

数据只来自 Xray 原生 StatsService：只读调用，始终 `reset=false`，管理端口只绑定回环。多个实例按各自的 `id`、配置路径、服务名和 API 地址分别配置（写法见 [部署模式](deployment.md)）。CI 用 Xray v26.10.21 跑原生测试；v26.3 起的核心都能接入，v26.10 以前的核心没有 `GetUsersStats`，面板会自动改为逐个用户查询在线地址。

| 统计对象 | 计数名 | 需要的统计开关 | 说明 |
| --- | --- | --- | --- |
| 入站（节点） | `inbound>>>tag>>>traffic>>>*` | `statsInboundUplink/Downlink` | 必需；实例总量、实时速率、分钟序列都只用入站计数 |
| 用户 | `user>>>email>>>traffic>>>*` | 用户等级的 `statsUserUplink/Downlink` | 用户需要有 `email`；同一 email 在多个入站共用一份计数 |
| 出站 | `outbound>>>tag>>>traffic>>>*` | `statsOutboundUplink/Downlink` | 出口卡片显示实测出站字节；与入站是同一批流量的两个观察点，不相加 |
| 在线地址 | `user>>>email>>>online` | 用户等级的 `statsUserOnline` | 只列出**当前有连接**的地址；核心不记录 `127.0.0.1` 和 `::1` |

缺少某项开关时，对应列显示“未提供”，不按 0 计算。三类计数各有独立的基线、每日累计和重启检测。

- `config_path` 指向该实例实际加载的主 JSON 配置。不支持 `-confdir` 这类多目录合并配置，也不去猜。只有能识别的入站和结构明确的路由才会显示对应能力。
- Xray 前面有独立的 Nginx 终止 TLS 时，可以设 `external_tls=true`、`external_port=443`。这两项只影响页面上的传输方式显示，不改节点。
- 能力边界：
  - 不提供逐连接字节、目标域名历史、按来源 IP 统计的字节。Xray 的 `SubscribeRoutingStats` 在核心里没有接上统计通道（`NewRoutingServer(router, nil)`），所以拿不到连接事件；
  - 不能断开单个连接；
  - 用户流量不分摊给任何 IP；
  - 主机网卡流量单独标注，不等同节点载荷。

## 1. 启用只读统计

### 自动：`scripts/enable-xray-stats.py`

```sh
.venv/bin/python -B scripts/enable-xray-stats.py [--instance ID] --check-only
.venv/bin/python -B scripts/enable-xray-stats.py [--instance ID]
```

对每个实例，脚本会：

1. 读取 systemd 单元**实际生效**的 ExecStart，找出所有 `-config` 文件。用 `-confdir` 或非 JSON 配置时拒绝处理。
2. 按 Xray 的多配置规则合并：后面文件的顶层对象会**整体替换**前面的同名对象（v26.3.27 和 v26.10.21 的 `run -dump` 都验证过）。所以叠加文件里要写入原配置最终生效的 `policy`，再加上统计开关：
   - `system.statsInboundUplink` / `statsInboundDownlink` / `statsOutboundUplink` / `statsOutboundDownlink`；
   - 入站用户用到的各 level 的 `statsUserUplink` / `statsUserDownlink` / `statsUserOnline`。
   
   原有的超时、缓冲等设置都保留。
3. 原配置没有 `api` 时，在 `api_address` 上增加只含 StatsService 的回环 API。已有 `api` 时：如果已经包含 StatsService 且地址一致，直接复用；否则拒绝处理，避免覆盖其他工具在用的 API。
4. 把叠加文件写进备份目录，连同原配置一起跑 `xray run -test`。`--check-only` 到这一步就停止。
5. 正式运行时：
   - 写入叠加文件，默认 `/etc/joysol-xray/<id>-stats.json`，0644，不含凭据；
   - 写入 drop-in `/etc/systemd/system/<服务>.d/zz-joysol-dashboard-stats.conf`，在原命令末尾追加 `-config <叠加文件>`；
   - `daemon-reload` 后重启 Xray，用 `xray api statsquery` 确认能读到入站计数；
   - 自检失败就恢复原 drop-in 和叠加文件，再重启一次。

脚本不修改原配置文件。输出只有摘要，不打印配置内容。备份在 `<backup_dir>/joysol-xray-stats-backup-<id>-<时间>/`。

### 手工

[xray-stats.example.json](../dashboard/deploy/xray-stats.example.json) 是叠加文件的最小示例。**注意：** 原配置里有 `policy` 时，叠加文件的 `policy` 会整个替换它。手工使用时，要先把原有的 `policy` 完整复制进叠加文件，再加统计开关。

```sh
sudo /usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json -config /etc/joysol-xray/main-stats.json
```

检查通过后，用 systemd drop-in 给**原 ExecStart** 追加第二个 `-config`，原命令的其他参数都要保留：

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json -config /etc/joysol-xray/main-stats.json
```

然后备份、`daemon-reload`、重启 Xray，确认原节点可用，且 API 只监听回环。在线来源依赖用户的 `email` 标识和核心支持，缺少时显示“未提供”。不要为了在线统计去改用户 UUID 或认证。

已经用旧版脚本启用过统计的实例，再运行一次脚本即可补上出站统计开关（同样会重启 Xray）。

## 2. 访问控制（可选）

默认关闭，面板只读。开启方法：在 `site.json` 里设 `settings.xray.access_control=true`，再运行部署脚本。

- **作用范围**：Xray 配置里监听公网地址的节点入站端口。TCP 端口用 `REJECT --reject-with tcp-reset`，`kcp` 传输、带 UDP 的 shadowsocks / socks 入站的 UDP 端口用 `DROP`。规则在 `JOYSOL_NODE_BANS` 链里，SSH 和面板端口不受影响。与 sing-box 共存时，两边的公网端口合并处理。
- **回环入口**：监听回环、经 Nginx 转发的入口（例如 WebSocket）不走 iptables，由面板的 Nginx `auth_request` 回源网关检查封禁，写法见 [面板](dashboard.md#封禁与真实-ip)。Xray 的 WebSocket 入站会读取 Nginx 传来的 `X-Forwarded-For`，在线地址因此是真实来源。
- **权限**：开启后，部署脚本会给面板单元加上 `CAP_NET_ADMIN` / `CAP_NET_RAW` 和 xtables 锁；关闭时只有 `CAP_DAC_READ_SEARCH`。
- **不改 Xray**：不写 Xray 配置，也不重启 Xray。Xray 的 `RoutingService.AddRule` 不追加时会替换全部路由规则，追加时排在最后、匹配不到，所以面板不用它封禁。
- 入站端口变化后，下一次封禁、解除或面板重启时会同步跳转规则。

## 3. 部署面板

推荐用 `scripts/deploy-dashboard.py`（见 [部署模式](deployment.md)），它负责预检、虚拟环境、备份、健康检查和回滚。

不用部署脚本时，可以按下面的步骤手工安装到全新目录。目录或配置已经存在就停止，不要覆盖：

```sh
sudo install -d -m 755 /opt/vps-dashboard/releases
panel_release="/opt/vps-dashboard/releases/$(date -u +%Y%m%dT%H%M%SZ)"
sudo mkdir "$panel_release"
sudo cp -a dashboard/app.py dashboard/panel dashboard/static "$panel_release/"
sudo ln -s "$panel_release" /opt/vps-dashboard/current
sudo python3 -m venv /opt/vps-dashboard/venv
sudo /opt/vps-dashboard/venv/bin/pip install -r dashboard/deploy/requirements.lock
sudo install -d -m 700 /etc/vps-dashboard /var/lib/vps-dashboard
sudo test ! -e /etc/vps-dashboard/settings.json
sudo install -m 600 dashboard/deploy/settings.xray.example.json /etc/vps-dashboard/settings.json
```

`settings.xray.example.json` 是服务器上的 **settings.json**，不是部署脚本用的 site.json。编辑其中的面板域名和 Xray 实例。然后在服务器本地生成面板密码哈希，密码不要写进命令参数或日志：

```sh
sudo python3 - <<'PY'
import getpass, hashlib, json, os
password = getpass.getpass('New dashboard password: ')
if not password:
    raise SystemExit('Password must not be empty')
salt = os.urandom(16).hex()
value = {'username': 'dashboard-admin', 'salt': salt,
         'password_hash': hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()}
fd = os.open('/etc/vps-dashboard/config.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as file:
    json.dump(value, file)
PY
sudo install -m 644 dashboard/deploy/vps-dashboard-xray.service /etc/systemd/system/vps-dashboard-xray.service
sudo systemctl daemon-reload
sudo systemctl enable --now vps-dashboard-xray.service
```

这个单元只监听 `127.0.0.1:8307`，保留严格沙箱，没有防火墙管理权限（手工安装时不支持访问控制；需要时改用部署脚本）。

## 4. HTTPS 与验收

在已有、证书有效的 Nginx HTTPS server 块里加一个明确的子路径，然后用实际的 Nginx 主配置检查并 reload：

```nginx
location = /monitor { return 302 /monitor/; }
location ^~ /monitor/ {
    proxy_pass http://127.0.0.1:8307;
    proxy_set_header Host $host;
    proxy_set_header X-Dashboard-Client-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    access_log off;
}
```

用实际域名和系统 CA 检查：登录、匿名访问被拒绝、CSRF、静态资源、Xray 实例状态，以及真实业务流量带来的计数增量。

- 不要把 8307 或 API 端口开放到公网，也不要关闭证书校验。
- 不要只凭服务 active 就认为成功。
- 经过 CDN 时，在线来源可能是 CDN 边缘或回环地址，不能当作真实客户端 IP，也不能当作每个用户的流量。

## 账本与探测

- **账本**：独立的 `xray-traffic.sqlite3`（0600），按实例和入站保存原始基线、进程代次、累计、每日和分钟增量；用户和出站另有各自的基线与每日累计，不进入分钟序列。
  - 首轮采样不回填历史。
  - 核心重启或计数归零、面板重启，都不会重复累计。
  - 采样中断跨日时，中断期间的流量计入恢复当天，不能当作精确的运营商账单。
- **探测**：每轮探测（默认每 5 分钟，最多 4 个并发）使用 `probes.targets`，和 sing-box 共用。
  - 只对能确定唯一出口的路由探测：入站只有一条无条件规则，出口是 freedom，或单一上游的 socks/http，且没有链式代理。条件路由、多上游、链式路由不假设出口。
  - trace 类目标的回显确认出口 IP，其他目标只检查 HTTPS 可达性。探测成功不代表视频能播放、能登录或解锁地区。
  - 探测历史最多保留 7 天。
- **用户流量告警**：`alerts.xray_user_daily_bytes`（系统页按 GB 填写，0 关闭），某个用户当天流量超过阈值时告警。
- **告警**：`alerts.probe_failures` 默认 2（0 表示关闭），按“出口 × 目标”统计连续失败的完成轮次。
  - 只有真实成功才算恢复。
  - 重复读取、标为已读或重启都不增加次数。
  - 数据缺失或过期会另发监控告警。

## 本地预览

```sh
python dashboard/dev/devserver.py --xray /path/to/xray
```

启动一个只监听回环的真实 Xray（两个 VLESS 用户串联）并持续产生流量，面板的 Xray 页面显示的是原生计数。核心不记录回环地址，所以预览里在线地址为空。

**升级核心前注意**：v26.10 起 `freedom` 出站默认拦截私有和回环目标，需要访问内网目标时要在 `finalRules` 里显式放行。

## 可选：公开的国内入口检测

默认关闭。开启方法：设 `reachability.enabled=true`，并在 `targets` 里配置：

```json
{"id": "entry", "label": "公开入口", "host": "panel.example.com", "path": "/monitor/login"}
```

- **检测方式**：每 600 秒，从电信 AS4134、联通 AS4837、移动 AS9808 各选一个可用的 Globalping 节点访问入口。某个运营商连续两轮失败就告警。间隔和轮数可以在系统设置里修改。
- **只接受公开页面**：路径只能是 `/`、`/monitor/login` 或 `/favicon.ico`，不能带查询串、订阅令牌、认证信息或 Cookie。检测请求会经过第三方公共平台，所以只能用公开目标。
- **判定标准**：要求节点在中国、ASN 匹配、HTTP 返回 2xx 且 TLS 验证通过。节点离线、缺失、限流或接口异常时显示“未知”并单独告警，不会算作目标断线，也不会算作恢复。
- **DNS 与故障位置**：默认指定阿里 DNS `223.5.5.5`，仅 DNS 失败时在同一探测点回退 DNSPod `119.29.29.29` 一次。可在文件中设置 `reachability.resolver` / `fallback_resolver`（公网 IPv4）；不修改服务器或客户端 DNS。出现故障后固定复测该城市，其他城市成功不会关闭原告警。TLS、HTTP 或下载失败不靠切换 DNS 判成恢复；收到 HTTP 200 后下载超时仍为失败。
- **平台异常**：各运营商独立保留结果，一个任务接口失败不会抹掉其他任务的成功；记录安全的 HTTP 状态和错误类型，遵守平台 `Retry-After`（最多一小时），不记录响应正文。探测配置的 DNS 发生变化时启动新一轮，但不绕过平台冷却期。
- **局限**：单个节点不代表整个运营商或全国。目前没有完整的订阅下载检查，入口能访问不等于订阅可用。


## 仅节点统计

设置 `xray.node_only=true` 后，面板只查询/累计节点入站计数，不再请求用户在线地址或更新用户/出站流量账本；既有账本保留。界面隐藏用户榜单、用户明细、用户/出站日统计和相关阈值，来源页改为节点在线状态，出口媒体探测继续保留。

“在线节点”表示近 60 秒内采样到该入站真实流量；使用各节点独立计数，不能由同一 email 或同一来源地址推断其他节点在线。最近活动时间持久保存，面板重启不丢失；无近期流量不表示链路不可用，采样过期/中断为未知。此状态不额外发起探测请求。
