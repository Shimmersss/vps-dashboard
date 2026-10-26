# vps-dashboard

代理服务器的运维脚本与浏览器管理面板，支持 **sing-box** 和 **Xray** 两种核心：可以只装其中一种，也可以两者共存，在同一面板里切换查看。面板实时显示流量、连接、链路拓扑、地理分布、网络质量和出口状态；运维脚本通过 SSH 部署、验证和导出客户端配置。

本仓库是从私有运维项目自动生成的脱敏副本：域名、服务器地址、端口、凭据都已替换为示例值（`example.com`、RFC 5737 文档地址），也不含部署记录、客户端配置和验证数据。

## 两种核心的能力

| 能力 | sing-box（连接管理） | Xray（节点监控） |
| --- | --- | --- |
| 数据来源 | 原生 API 连接事件 | 原生只读 StatsService，默认 2 秒采样，`reset=false` |
| 流量 | 逐连接：来源 IP、目标、入站、出站 | 按实例 / 入站：累计、今日、实时速率 |
| 拓扑 / 地图 | 客户端 → 入口 → 规则 → 出口 | 配置中的入站 / 出站与实测出口；不虚构客户端位置 |
| 历史 | 连接汇总、分钟 / 小时序列 | 独立账本：实例分钟 / 小时增量、自然日统计 |
| 断开 / 封禁 | 单连接断开；iptables 封禁（TCP / UDP 入站）；CDN 回源网关 | 只读，不提供 |
| 出口探测 | 经各出站请求探测目标，trace 回显确认出口 IP | 同一套目标，只对能确定唯一出口的路由探测 |
| 告警 / 账号 / 审计 | 共用 | 共用 |

两种统计口径相互独立：Xray 缺失或过期的数据显示“未知”，不会用 sing-box 的数据补齐。

## 部署方式

| 模式 | site.json 设置 | 说明 |
| --- | --- | --- |
| 只用 sing-box | 默认 | 原有部署方式 |
| 只用 Xray | `settings.singbox.enabled=false`，并填写 `settings.xray.instances` | 先用 `enable-xray-stats.py` 打开回环统计接口；面板不需要 sing-box，也没有防火墙权限 |
| 两者共存 | 保留 sing-box，再填写 `settings.xray.instances` | 默认显示 Xray，侧栏可以切换数据来源 |

三种模式都用同一个部署脚本，它会先检查服务器，再备份，最后切换版本，失败会自动回滚；新服务器也能直接部署。详见 [部署模式](docs/deployment.md)。

## 文档

- [部署模式](docs/deployment.md)：三种模式的步骤、共存原则、验收与回退。
- [Xray 接入](docs/xray.md)：启用统计接口、手工安装、账本与探测规则、能力边界。
- [面板](docs/dashboard.md)：页面、统计口径、配置分层、封禁原理、发布流程。
- [运维脚本](docs/operations.md)：环境准备、`./ops` 用法、远端变更流程、脚本一览。

## 快速开始

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock
cp .env.example .env && chmod 600 .env        # 填写 SSH 目标
cp dashboard/deploy/site.example.json dashboard/deploy/site.json   # 填写域名、路径和部署模式
./ops --check-local
./ops --test-connection
.venv/bin/python -B scripts/deploy-dashboard.py --check-only
```

本地预览面板（sing-box 合成数据，不连接任何服务器）：`python dashboard/dev/devserver.py`。

## 测试

```sh
cd dashboard/tests
XRAY_TEST_BINARY=/path/to/xray python -B -W ignore::ResourceWarning -m unittest \
  test_panel test_media_probes test_xray test_xray_dashboard test_alert_sources test_engine_modes
cd ../web && npm ci && node --test tests/engines.test.mjs && npm run build
```

`test_xray` 里的原生测试会在回环地址、临时目录里用虚构配置启动一个真实的 Xray，验证 gRPC 接口和计数。CI（`.github/workflows/dashboard.yml`）固定使用 Xray v26.3.27，并核对官方发布包的 SHA256。

## 安全说明

- `.env`、`exports/`、`verification/`、`.ssh/`、`dashboard/deploy/site.json` 已在 `.gitignore` 中排除，这些文件里是凭据、节点配置和验证证据。
- 面板和各核心的 API 只监听回环地址，经已有 Nginx HTTPS 子路径访问。登录密码只存盐和 PBKDF2 哈希。
- 远端修改的约定：先只读核验，再备份；`sing-box check`、`xray run -test`、`nginx -t` 通过后才重启，并提前说明连接会瞬断。
- 可选的“国内入口检测”默认关闭；开启后只向公共探测平台提交无令牌的公开页面。
- 第三方数据与许可证：ip2region（Apache-2.0，见 `dashboard/deploy/geo/LICENSE-ip2region.md`），DB-IP Lite（CC BY 4.0）。
