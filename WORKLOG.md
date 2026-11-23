# WORKLOG

## 2026-11-02: Fix chart colors during initial page load

- Wait for page stylesheets before reading CSS theme tokens and mounting charts; report loading failures rather than caching empty colors.
- Read computed styles once per refresh and validate every theme color before publishing the palette.
- Add regression coverage for delayed, cached/disabled and failed stylesheet loads; all 11 frontend tests pass.
- Rebuild the tracked production assets. Production build and Safari visual checks passed for dark/light overview charts and the light map after reloading.

## 2026-11-23: Show CDN edges only when configured

- Treat loopback WebSocket inbounds as Nginx proxy entries unless deployment settings or an inbound override identify a CDN.
- Show the configured CDN provider when present and remove the fixed ESA label and port.
- Cover the default, gateway, provider and override cases, including the CDN gate notice, and rebuild the production assets.
