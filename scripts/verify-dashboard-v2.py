"""Public checks for panel v2 features; prints aggregates only (no client IPs, no secrets).

Covers: overview/topology APIs, realtime stream through the CDN (or polling fallback), exit probes,
asset compression/caching, geolocation sources. Evidence: verification/dashboard/v2-check.json.
"""
import http.cookiejar
import json
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

import siteprofile

ROOT = Path(__file__).resolve().parents[1]
profile = siteprofile.load()
access = json.loads((ROOT / 'exports/dashboard-access.json').read_text())
base = profile['panel_url'].rstrip('/')
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))


def req(path, body=None, headers=None, method=None):
    return opener.open(urllib.request.Request(base + path, data=body, headers=headers or {}, method=method), timeout=60)


def get(path):
    with req(path) as r:
        return json.load(r)


evidence = {}
form = urllib.parse.urlencode({'username': access['username'], 'password': access['password'], 'remember': 'on'}).encode()
req('/login', form, {'Origin': profile['origin'], 'Content-Type': 'application/x-www-form-urlencoded'}).read()
overview = get('/api/overview')
csrf = overview['csrf']
evidence['overview'] = {'version': overview['version'], 'collector': overview['health']['connected'], 'gate': overview['gate'],
                        'inbounds': [i['tag'] for i in overview['topology']['inbounds']],
                        'outbounds': [o['tag'] for o in overview['topology']['outbounds']]}

# Realtime stream through the CDN: count frames within a few seconds.
cookie = '; '.join('%s=%s' % (c.name, c.value) for c in jar)
parts = urllib.parse.urlsplit(base)
frames, first_ms = 0, None
started = time.monotonic()
try:
    with socket.create_connection((parts.hostname, 443), timeout=10) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname) as s:
            s.sendall(('GET %s/api/stream HTTP/1.1\r\nHost: %s\r\nCookie: %s\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n' % (
                parts.path, parts.hostname, cookie)).encode())
            s.settimeout(2)
            buf = b''
            while time.monotonic() - started < 8:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                count = buf.count(b'event: frame')
                if count and first_ms is None:
                    first_ms = int((time.monotonic() - started) * 1000)
                frames = count
except OSError as exc:
    evidence['stream_error'] = type(exc).__name__
evidence['stream'] = {'frames_in_8s': frames, 'first_frame_ms': first_ms, 'usable': frames >= 3}
live = get('/api/live?seconds=60')
evidence['polling_fallback'] = {'samples': len(live['series']), 'active': live['frame']['active']}

# Exit probes (a few KB through each upstream).
with req('/api/probes/run', b'{}', {'Content-Type': 'application/json', 'Origin': profile['origin'], 'X-CSRF-Token': csrf}) as r:
    probes = json.load(r)['latest']
evidence['probes'] = {tag: {'ok': p['ok'], 'exit_ip': p['exit_ip'], 'location': (p.get('geo') or {}).get('location'),
                            'targets': {k: (v['ok'], v['total_ms']) for k, v in p['targets'].items()}} for tag, p in probes.items()}

topo = get('/api/topology?range=24h')
evidence['topology'] = {'flows': len(topo['flows']), 'with_rule': sum(1 for f in topo['flows'] if f['rule']),
                        'server_exit_known': bool(topo.get('server') and topo['server'].get('exit_ip'))}

net = get('/api/network?range=1h')
today = net['totals']['today']
evidence['network'] = {
    'interfaces': net['interfaces'], 'live_samples': len(net['live']), 'nic_rate_now': net['current'],
    'today_nic_vs_payload': round((today['rx'] + today['tx']) / today['payload'], 2) if today['payload'] else None,
    'quality_sampled_at': net['quality']['latest']['at'], 'quality_error': net['quality']['latest']['error'],
    'segments': {k: {'rtt_ms': v['rtt'], 'conns': v['conns'], 'keys': v['keys'], 'retrans_pct': v['retrans_pct']}
                 for k, v in net['quality']['latest']['segments'].items()},
    'edge_ports': net['quality']['edge_ports'], 'public_ports': net['quality']['public_ports'],
}

ips = get('/api/ips')['rows']
evidence['geo'] = {'clients': len(ips), 'sources': sorted({get('/api/geo/' + r['ip'])['source'] or 'none' for r in ips}),
                   'with_isp': sum(1 for r in ips if r['isp'] not in ('', '未知'))}

# Assets: hashed bundle served compressed and cacheable.
with req('/') as r:
    html = r.read().decode()
asset = re.search(r'assets/(index-[^"]+\.js)', html).group(1)
with opener.open(urllib.request.Request(base + '/assets/' + asset, headers={'Accept-Encoding': 'gzip'}), timeout=60) as r:
    evidence['asset'] = {'encoding': r.headers.get('Content-Encoding'), 'cache': r.headers.get('Cache-Control'), 'bytes': len(r.read())}
evidence['security_headers'] = {}
with req('/api/overview') as r:
    evidence['security_headers'] = {k: r.headers.get(k) for k in ('Cache-Control', 'Content-Security-Policy', 'X-Frame-Options')}

with req('/logout', b'{}', {'Content-Type': 'application/json', 'Origin': profile['origin'], 'X-CSRF-Token': csrf}) as r:
    r.read()
out = ROOT / 'verification/dashboard/v2-check.json'
out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
out.chmod(0o600)
print(json.dumps(evidence, indent=2, ensure_ascii=False))
