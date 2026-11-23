# WORKLOG

## 2026-11-23：修复 sing-box TLS 上游探测与来源统计

- 支持读取分文件配置，并按出站标签配置探测目标。修复 HTTPS 上游代理内的目标 TLS 握手，兼容预期认证响应和旧探测账本。
- 补充 Nginx Stream 共用 TCP 443 时的 AnyTLS 真实来源地址、Linux 回程路由和回滚说明。
- 通过 81 项面板测试，覆盖按出口选择探测目标及认证响应判断。

## 2026-11-02: Fix chart colors during initial page load

- Wait for page stylesheets before reading CSS theme tokens and mounting charts; report loading failures rather than caching empty colors.
- Read computed styles once per refresh and validate every theme color before publishing the palette.
- Add regression coverage for delayed, cached/disabled and failed stylesheet loads; all 11 frontend tests pass.
- Rebuild the tracked production assets. Production build and Safari visual checks passed for dark/light overview charts and the light map after reloading.

## 2026-11-23: Show CDN edges only when configured

- Treat loopback WebSocket inbounds as Nginx proxy entries unless deployment settings or an inbound override identify a CDN.
- Show the configured CDN provider when present and remove the fixed ESA label and port.
- Cover the default, gateway, provider and override cases, including the CDN gate notice, and rebuild the production assets.
