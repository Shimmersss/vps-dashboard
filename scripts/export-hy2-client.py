"""Export Hysteria2 client configs to exports/hy2/ (0600); never prints the node password.

Usage: .venv/bin/python -B scripts/export-hy2-client.py [--up 10] [--down 10]
--up/--down (Mbps) are the client's declared bandwidth for Hysteria Brutal; set them near the real line
speed (too high wastes bandwidth on loss, too low caps throughput). 0 lets the server fall back to BBR.
"""
import argparse
import json
import os
from urllib.parse import quote, urlencode

from client_rules import CN_CLASH_RULES, china_split, clash_rules, sing_rules
from remote import ROOT, connect, m

parser = argparse.ArgumentParser()
parser.add_argument('--up', type=int, default=10)
parser.add_argument('--down', type=int, default=10)
args = parser.parse_args()
out = ROOT / 'exports/hy2'
out.mkdir(mode=0o700, exist_ok=True)
out.chmod(0o700)


def save(name, content):
    fd = os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(content)


with connect()[0] as c:
    with c.open_sftp() as sftp:
        with sftp.open('/root/joysol-hy2-client.json') as f:
            p = json.load(f)
host = m.load_settings(ROOT / '.env')['SSH_HOST']
name = p['name']
query = {'sni': p['server_name'], 'alpn': ','.join(p['alpn'])}
if args.up and args.down:
    query.update(upmbps=args.up, downmbps=args.down)
save('hysteria2.txt', 'hysteria2://%s@%s:%d/?%s#%s\n' % (quote(p['password'], safe=''), host, p['port'], urlencode(query), quote(name)))

proxy = {'type': 'hysteria2', 'tag': 'proxy', 'server': host, 'server_port': p['port'], 'password': p['password'],
         'tls': {'enabled': True, 'server_name': p['server_name'], 'alpn': p['alpn']}}
if args.up and args.down:
    proxy.update(up_mbps=args.up, down_mbps=args.down)
client = {'log': {'level': 'warn'}, 'inbounds': [{'type': 'mixed', 'tag': 'mixed-in', 'listen': '127.0.0.1', 'listen_port': 7890}],
          'outbounds': [proxy, {'type': 'direct', 'tag': 'direct'}],
          'route': {'rules': sing_rules() + [{'ip_is_private': True, 'action': 'route', 'outbound': 'direct'}], 'final': 'proxy'}}
save('sing-box-client.json', json.dumps(china_split(client, 'sing'), indent=2) + '\n')

q = json.dumps
bandwidth = '    up: "%d Mbps"\n    down: "%d Mbps"\n' % (args.up, args.down) if args.up and args.down else ''
clash = f'''mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
find-process-mode: strict
log-level: warning
ipv6: false
proxies:
  - name: {name}
    type: hysteria2
    server: {q(host)}
    port: {p['port']}
    password: {q(p['password'])}
{bandwidth}    sni: {q(p['server_name'])}
    alpn: [h3]
    udp: true
proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - {name}
rules:
{chr(10).join('  - ' + rule for rule in clash_rules())}
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
{chr(10).join('  - ' + rule for rule in CN_CLASH_RULES)}
  - MATCH,PROXY
'''
save('clash-verge.yaml', clash)
save('README.txt', '''代理服务：Hysteria2（QUIC / UDP %d），与 Reality 节点同一台服务器，证书域名 %s。
Clash Verge Rev / Mihomo：从本地文件导入 clash-verge.yaml。
小火箭 / v2rayN 等：复制 hysteria2.txt 的内容，从剪贴板导入。
sing-box：导入 sing-box-client.json，本机 127.0.0.1:7890 混合代理。
带宽声明：上行 %s、下行 %s Mbps（Hysteria Brutal 按此速率发送，丢包时不降速）。实际线路更慢时请调低。
部分网络会对 UDP 限速或封锁；连不上时换回 Reality 或 ESA 节点。
规则与服务端分流同 Reality 节点。文件含节点密码，请私密保存。
''' % (p['port'], p['server_name'], args.up or '-', args.down or '-'))
print('Exported Hysteria2 client configs to exports/hy2/; password omitted from console.')
