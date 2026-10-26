# Xray 节点监控版

## 功能范围

Xray 主界面覆盖概览、节点、速率、配置拓扑、实测出口地图、在线来源、流量采样记录、自然日流量、出口探测、网卡和系统状态。StatsService 只读调用始终 `reset=false`；管理端口只绑定回环。多实例按独立 `id`、配置路径、服务与 API 地址配置。

- `settings.xray.example.json` 用于**服务器上的 settings.json**，不是旧部署器的 site.json。它关闭 sing-box，无需安装该核心或配置其 API 密钥。
- 如要共存，设 `singbox.enabled=true` 并保留原 sing-box 配置/API。配置了 Xray 后默认显示 Xray，界面内切换不会重启或改写任何核心。
- `config_path` 应指向当前实例完整、实际生效的 Xray 基础 JSON（不支持猜测多目录合并配置）。仅支持已识别入站和明确结构化路由的能力。
- Xray 经过独立 Nginx 终止 TLS 时，可设置 `external_tls=true`、`external_port=443`，这仅描述传输展示，不修改节点。

## 1. 已有 Xray 启用只读统计

先确认实际 Xray 版本、服务用户、完整 ExecStart、配置、已有 API 与空闲端口。以下示例路径需按实际安装调整；不要直接替换带额外参数的现有命令。

将 [xray-stats.example.json](../dashboard/deploy/xray-stats.example.json) 保存为独立受保护配置。它仅增加 stats、统计 policy 和 `127.0.0.1:8311` 的 StatsService；没有 HandlerService、RoutingService。若已有 API，需将 StatsService 整合到已有配置并使用实际监听地址，不应直接叠加。

```sh
# 示例：实际服务用户必须可读 overlay；保留原文件，不覆盖原节点配置。
sudo install -d -m 750 /etc/xray
sudo test ! -e /etc/xray/dashboard-stats.json
sudo install -m 600 dashboard/deploy/xray-stats.example.json /etc/xray/dashboard-stats.json
# 根据原 Xray 用户设置 overlay 的所有者/组和最小读取权限，再执行完整配置检查。
sudo /usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json -config /etc/xray/dashboard-stats.json
```

检查通过后，使用 systemd drop-in 给**实际原 ExecStart**追加第二个 `-config`。例如原命令恰好为下列形式时：

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json -config /etc/xray/dashboard-stats.json
```

保留原命令所有其他参数，备份实际 unit/配置，然后 daemon-reload 并重启对应 Xray。核对原节点可用性及 API 只监听回环。非零用户 level 需按实际 policy 开启对应统计；在线来源依赖用户标识/核心支持，缺少时保持未提供。不要为了在线统计重写用户 UUID 或认证。

## 2. 独立安装面板

依赖 Python 3.10+、venv 与 systemd。已有部署先备份并走自己的升级流程；下列示例用于全新面板目录，若 current/配置已存在就停止，不能直接覆盖。

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

编辑 settings 中的实际面板域名、Xray service/config/API；默认只有 Xray。在服务器本地生成面板密码哈希，密码不进参数或日志：

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

单元只监听 `127.0.0.1:8307`，保留严格沙箱，没有防火墙管理权限。示例用 root 只读受保护配置；若更换专用用户，须单独授予 Xray 配置读取、data 写入和实际系统状态读取权限，不能放宽配置到全局可读。

## 3. HTTPS 与验收

在已有、证书有效的 Nginx HTTPS server 块添加明确子路径，使用实际 Nginx 主配置检查和 reload：

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

使用实际域名/system CA 检查登录、匿名拒绝、CSRF、静态资源、Xray 实例与真实业务流量增量。不要开放 8307/8311 到公网、关闭证书校验或仅凭服务 active 宣称成功。CDN 后的来源可能是边缘/回环，不能当作真实客户端 IP 或每个用户的字节。

## 账本与探测

- 独立 `xray-traffic.sqlite3`（0600）按实例/入站保存原始基线、进程代次、累计、每日与分钟增量。首轮不回填；核心/单方向归零和面板重启不重复累计。中断跨日的流量计入恢复日，不能冒充精确运营商账单。
- 不提供原生逐连接字节、目标域名历史、逐来源字节、连接关闭或 Xray WS/CDN 封禁；访问控制页明确只读。主机网卡流量单独标注，不等同节点载荷。
- 每 5 分钟、最多 4 并发，经可证明的单一独立直连/上游路由探测 Cloudflare 出口回显和三家媒体 HTTPS。条件、多上游、链式路由不假设一个出口。探测历史最多 7 天；媒体探测不是吞吐/播放/地区解锁检查。
- `alerts.probe_failures` 默认 2（0 关闭），按出口/网站及完成轮次告警。实际成功才恢复；重复读取、已读或重启不增加次数；异常/过期数据另发监控告警。

## 可选公开大陆入口检测

默认关闭。要使用，设置 `reachability.enabled=true` 并在 `targets` 配置 `{ "id": "entry", "label": "公开入口", "host": "panel.example.com", "path": "/monitor/login" }`。每 600 秒从大陆电信 AS4134、联通 AS4837、移动 AS9808 各选一个可用 Globalping 节点；分别连续两轮失败告警。间隔/轮数可在 Xray 系统设置修改。

公开测量只接受 `/`、`/monitor/login` 或 `/favicon.ico`，禁止查询串/订阅令牌、认证或 Cookie；结果会经过第三方公共探测平台，因此只能用公开目标。核对 CN、ASN、HTTP 2xx 与 TLS authorized。离线、缺失、限流/API 异常显示未知并单独告警，不伪装目标断线或恢复。单个节点不代表整个运营商/全国；当前没有完整订阅下载检查，不能把入口 HTTPS 结果当成订阅验收。
