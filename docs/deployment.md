# 部署模式

面板可以只接 sing-box、只接 Xray，或者两者都接。三种模式都用 `scripts/deploy-dashboard.py` 部署，模式由 `dashboard/deploy/site.json` 的 `settings` 决定：

| 模式 | `settings.singbox.enabled` | `settings.xray.instances` | 面板默认显示 |
| --- | --- | --- | --- |
| 只用 sing-box | `true`（默认） | 空 | sing-box |
| 只用 Xray | `false` | 至少一个实例 | Xray |
| 两者共存 | `true` | 至少一个实例 | Xray，侧栏可切换 |

Xray 实例的写法：

```json
"xray": {
  "poll_seconds": 2,
  "instances": [
    {"id": "main", "label": "Xray 主入口", "service": "xray.service",
     "config_path": "/usr/local/etc/xray/config.json", "api_address": "127.0.0.1:8311",
     "external_tls": false, "node_labels": {}}
  ]
}
```

- `id` 只能用字母、数字、`_`、`-`；多个实例的 `id` 和 `api_address` 不能重复。
- `config_path` 是该实例实际加载的主配置文件；面板从这里读取入站和出站。
- `api_address` 必须是回环地址。
- 可选 `stats_overlay`：统计叠加文件的位置，默认 `/etc/joysol-xray/<id>-stats.json`。
- 可选 `settings.xray.access_control`（默认 `false`）：封禁 Xray 公网入站端口的来源地址，见 [Xray 接入](xray.md#2-访问控制可选)。

## 只用 sing-box

保持原有流程即可：

```sh
.venv/bin/python -B scripts/deploy-dashboard.py --check-only   # 只读预检
.venv/bin/python -B scripts/deploy-dashboard.py                # 部署，只重启面板
```

部署脚本会在 sing-box 配置里维护一个只监听回环的 API 服务。这个服务块有变化时才重启 sing-box（通常不会）。

## 只用 Xray

1. 在 `site.json` 里设 `settings.singbox.enabled=false`，并填写 `settings.xray.instances`。`remote` 里的 `singbox_*` 字段可以不填。
2. 打开 Xray 的回环统计接口（详见 [Xray 接入](xray.md)）：

   ```sh
   .venv/bin/python -B scripts/enable-xray-stats.py --check-only   # 生成叠加文件并 xray run -test，不改服务
   .venv/bin/python -B scripts/enable-xray-stats.py                # 写 systemd drop-in 并重启 Xray，现有连接会断几秒
   ```

3. 部署面板：

   ```sh
   .venv/bin/python -B scripts/deploy-dashboard.py --check-only
   .venv/bin/python -B scripts/deploy-dashboard.py
   ```

这个模式下，面板不读取 sing-box 配置，不启动连接采集和 sing-box 出口探测，对应的接口会明确拒绝。默认 systemd 单元只保留读取配置所需的 `CAP_DAC_READ_SEARCH`，没有防火墙权限，也不依赖 `/run/xtables.lock`；设了 `xray.access_control=true` 时才加上防火墙权限，封禁作用于 Xray 的公网入站端口。

## 两者共存

1. 保留 `settings.singbox.enabled=true`，再填写 `settings.xray.instances`。
2. 对每个 Xray 实例运行 `enable-xray-stats.py`。sing-box 和 Xray 的 API 要用不同的回环端口。
3. 运行 `deploy-dashboard.py`。健康检查要求 sing-box 采集正常、所有 Xray 实例都采样成功，才算部署成功。

登录后，侧栏的“监控来源”可以切换查看两个核心的数据。切换只改变显示，不会重启或改写任何核心。封禁默认只作用于 sing-box 的入站端口；设了 `xray.access_control=true` 后，Xray 的公网入站端口也加入同一条防火墙链。

两种来源均支持逐网站媒体故障、探测异常/过期告警；来源专属告警的列表、未读数和批量已读相互独立。证书、磁盘与可选大陆公开入口 HTTPS 检测按站点共享，后者不代表节点协议或完整订阅下载可用。详见 [sing-box 告警与探测](dashboard.md) 和 [Xray 接入](xray.md)。

## 部署脚本做什么

1. **只读预检**：
   - Nginx `auth_request` 模块和磁盘空间（至少 1 GiB）；
   - 指定的 Nginx 配置里，有没有 `listen <origin_tls_port> ssl` 且 `server_name` 是面板域名的 server 块；
   - sing-box 配置；
   - 每个 Xray 实例的服务状态、配置是否可读、回环统计接口是否在监听；
   - 面板虚拟环境和依赖是否齐全。

   任何一项不满足就停止，不改动服务器。
2. **上传版本**：上传到 `releases/<时间>`。虚拟环境不存在就新建；依赖缺失时，按 `dashboard/deploy/requirements.lock` 安装。依赖齐全的虚拟环境不会被升级。
3. **远端测试**：在服务器上跑离线测试。有 Xray 时，还会用服务器上的 Xray 在回环地址跑原生测试。已有账本时，在内存里演练数据库迁移并核对汇总。
4. **备份**：配置目录、systemd 单元、Nginx 配置、sing-box 配置、旧代码，以及两个账本（`stats.sqlite3` 和 `xray-traffic.sqlite3`，用 SQLite backup API 备份）。新服务器上不存在的文件会跳过。
5. **写入**：写配置和单元，`nginx -t` 通过后才继续。
6. **切换**：切换版本，只重启面板，然后做健康检查和登录页检查，最后 reload Nginx。

健康检查失败时自动回滚，恢复到备份的版本、配置、单元和账本。如果是首次安装，回滚会移除新单元和 `current` 链接。

**瞬断影响**：
- 每次部署会重启面板约 5–15 秒，期间 CDN 入口的新连接无法通过回源校验（只在启用了 sing-box 网关时有影响）。
- `enable-xray-stats.py` 正式运行时会重启 Xray，Xray 的现有连接会断几秒。

## 共存原则

1. 先只读确认实际在跑的服务、完整配置、已有 API、Nginx 主配置和监听端口，再备份。不要凭目录名猜测服务归属。
2. 面板不接管节点认证、上游、路由、TLS 网站，也不接管其他管理工具的数据。
3. 已有 Xray API 时，脚本拒绝自动处理，需要手工把 StatsService 整合进原有 API，不能再叠加第二个 API。
4. SSH 断线不代表远端变更没有完成。先只读确认版本、备份、监听和健康状态，再决定是否恢复，不要盲目重跑。
5. Xray 账本和 sing-box 账本相互独立，不能互相替代或合并。

## 验收与回退

- 用实际域名和系统 CA 访问 HTTPS，逐项检查：登录、匿名访问被拒绝、CSRF、静态资源、来源切换、真实计数和探测。
- 计数要先记基线，再经节点协议做一次真实请求，核对增量。面板重启不应重置核心计数，也不应重复累计。
- 面板版本回退：把 `current` 链接指回上一个 release，然后重启面板服务。
- 关闭 Xray 访问控制：先在面板里解除相关封禁，再把 `xray.access_control` 设回 `false` 并重新部署。
- 撤销 Xray 统计：删除 `/etc/systemd/system/<服务>.d/zz-joysol-dashboard-stats.conf`，再执行 `systemctl daemon-reload` 并重启 Xray。叠加文件可以保留，也可以删除。
