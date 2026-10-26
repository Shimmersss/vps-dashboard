# vps-dashboard

sing-box 代理服务器的运维脚本与浏览器管理面板。面板实时显示流量、连接、链路拓扑、地理分布、网络质量和出口状态，可封禁 IP、断开连接、管理告警。运维脚本通过 SSH 部署、验证和导出客户端配置。

本仓库是从私有运维项目自动生成的脱敏副本：域名、服务器地址、端口、凭据都已替换为示例值（`example.com`、RFC 5737 文档地址），也不含部署记录、客户端配置和验证数据。

## 功能

- **面板**（`dashboard/`）：Flask + gunicorn 后端，Vue 3 + ECharts 前端。数据来自 sing-box 原生 API 的连接事件，账本为 SQLite。
  - 概览、实时连接、链路拓扑（桑基图）、地理分布地图、客户端与连接历史、每日流量、网络质量、出口探测、告警、会话与审计。
  - 封禁单个 IP 或网段，可设到期。规则写入独立 iptables 链，只覆盖公网入站端口（TCP 与 UDP 入站都支持）。CDN 入口通过 Nginx `auth_request` 网关识别真实客户端 IP。
  - 离线 IP 定位（ip2region + DB-IP Lite），不向第三方发送客户端地址。
  - 代码不写死域名、地址、端口或 sing-box 标签：站点配置来自 `dashboard/deploy/site.json`（示例见 `site.example.json`），入站、出站和规则在运行时从 sing-box 配置读取。
- **运维入口**（`./ops`）：SSH 只读检查和命令批量执行，首次连接采用 TOFU 主机密钥校验。
- **部署与维护脚本**（`scripts/`）：sing-box 入站部署（VLESS Reality、VLESS WS 经 CDN、Hysteria2）、AI 流量分流到上游代理、TCP 参数调优、面板部署与验证、客户端配置导出、订阅发布。

## 文档

- [面板](docs/dashboard.md)：页面、统计口径、配置分层、封禁原理、部署与回退。
- [运维脚本](docs/operations.md)：环境准备、`./ops` 用法、远端变更流程、脚本一览。

## 快速开始

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock
cp .env.example .env && chmod 600 .env        # 填写 SSH 目标
cp dashboard/deploy/site.example.json dashboard/deploy/site.json   # 填写域名与路径
./ops --check-local
./ops --test-connection
.venv/bin/python -B scripts/deploy-dashboard.py --check-only
```

本地预览面板（合成数据，不连接任何服务器）：`python dashboard/dev/devserver.py`。

## 安全说明

- `.env`、`exports/`、`verification/`、`.ssh/` 已在 `.gitignore` 中排除，里面是凭据、节点配置和验证证据。
- 远端修改的约定：先只读核验，再备份；`sing-box check` / `nginx -t` 通过后才重启，并提前说明连接会瞬断。
- 第三方数据与许可证：ip2region（Apache-2.0，见 `dashboard/deploy/geo/LICENSE-ip2region.md`），DB-IP Lite（CC BY 4.0）。
