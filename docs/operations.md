# 运维脚本

## 环境

- Python 3.13 虚拟环境，依赖固定在 `requirements.lock`：

  ```sh
  uv venv .venv
  uv pip install --python .venv/bin/python -r requirements.lock
  ```

- `.env` 按 `.env.example` 填写 SSH 目标，权限必须是 0600。文件不会当作 shell 脚本执行；密码里有引号、换行或反斜线时用 JSON 字符串转义。同名环境变量会覆盖文件中的值。不要把密码写在命令行参数里。
- 服务器地址只来自 `.env`；域名、路径和远端目录来自 `dashboard/deploy/site.json`。

## `./ops`

```sh
./ops --check-local                      # 本地依赖与配置
./ops --test-connection                  # 登录与主机密钥
./ops --commands examples/read-only.json # 只读检查系统与 sing-box 服务
./ops --commands examples/read-only.json --output /tmp/inspection.json
```

- 首次连接新主机时加 `--accept-new-host-key`：采用首次信任（TOFU），打印 SHA256 指纹并保存到 `.ssh/known_hosts`。之后每次连接都校验，密钥变了就拒绝连接。建议通过独立渠道核对首次指纹。
- 输出文件权限为 0600，可能包含远端敏感内容。
- 每条命令默认超时 60 秒；遇到非零退出码立即停止，整体返回非零。超时或断线不代表远端进程已停止，重试前先只读确认状态。

## 远端变更流程

1. 先只读核验实际服务和配置，再确定变更范围。
2. 备份相关配置。
3. `sing-box check` / `nginx -t` 通过后才重启或 reload。
4. 重启前说明影响：重启 sing-box 会中断现有代理连接几秒；重启面板会让 CDN 入口的新连接短暂失败。
5. 用出口地址、全链路请求和服务状态验证结果，保留回滚步骤。

## 脚本一览

| 脚本 | 作用 |
| --- | --- |
| `deploy-server.sh` | 首次部署 sing-box（VLESS Reality），校验官方发布包 SHA256 |
| `prepare-esa-origin.sh`、`finalize-esa-origin.sh` | CDN 回源：Nginx HTTPS 源站 + 回环 WebSocket 入站 + certbot 证书 |
| `deploy-hysteria2.py` | 增加 Hysteria2（QUIC/UDP）入站，复用站点证书，安装续期钩子；`--check-only` 只做候选配置校验 |
| `deploy-oxylabs-esa.py` | 增加经 CDN 的变体入站，AI 流量走指定上游代理 |
| `configure-ai-exit.py` | 服务端把 AI 服务域名分流到上游家宽代理 |
| `enable-bbr.sh`、`tune-tcp-buffers.sh` | BBR 与 TCP 缓冲调优，自动备份原值，支持回退 |
| `deploy-dashboard.py` | 部署面板（预检、测试、迁移演练、备份、自动回滚） |
| `sync-dashboard-login.py` | 同步面板主账户密码并撤销所有会话 |
| `export-client.py`、`export-esa-client.py`、`export-hy2-client.py` | 导出 Clash/Mihomo、sing-box 和 URI 客户端配置到私有 `exports/` |
| `publish-subscriptions.py` | 把导出的配置发布为带随机令牌的 HTTPS 订阅 |
| `apply-china-split.py`、`client_rules.py` | 客户端“国内直连、国外代理”规则 |
| `verify-*.py` | 端到端验证：代理链路、CDN 网关、面板、订阅、分流 |
| `benchmark-proxy.py` | 经本机 Mihomo 对比节点延迟与下载速度 |
| `publish-public.py` | 生成本仓库的脱敏副本并推送 |

所有导出和验证结果都写入 `exports/` 或 `verification/`（0600），控制台只打印汇总，不打印凭据。
