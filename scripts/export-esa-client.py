from client_rules import clash_rules,sing_rules,china_split,CN_CLASH_RULES
"""Export the ESA WebSocket node; no SSH or upstream credentials are exported."""
import json,os,zipfile
from urllib.parse import urlencode,quote
from remote import ROOT,connect
out=ROOT/'exports/esa';out.mkdir(mode=0o700,exist_ok=True);out.chmod(0o700)
with connect()[0] as ssh:
 with ssh.open_sftp() as sftp:
  with sftp.open('/root/joysol-esa-client.json') as f:p=json.load(f)
def save(name,text):
 fd=os.open(out/name,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);f.write(text)
query=urlencode({'encryption':'none','security':'tls','sni':p['server'],'fp':'chrome','type':'ws','host':p['server'],'path':p['path']})
save('vless-esa.txt',f"vless://{p['uuid']}@{p['server']}:443?{query}#"+quote('Joysol-ESA')+'\n')
proxy={'type':'vless','tag':'proxy','server':p['server'],'server_port':443,'uuid':p['uuid'],'tls':{'enabled':True,'server_name':p['server'],'utls':{'enabled':True,'fingerprint':'chrome'}},'transport':{'type':'ws','path':p['path'],'headers':{'Host':p['server']}}}
client={'log':{'level':'warn'},'inbounds':[{'type':'mixed','tag':'mixed-in','listen':'127.0.0.1','listen_port':7890}],'outbounds':[proxy,{'type':'direct','tag':'direct'}],'route':{'rules':sing_rules()+[{'ip_is_private':True,'action':'route','outbound':'direct'}],'final':'proxy'}}
save('sing-box-client.json',json.dumps(china_split(client,'sing'),indent=2)+'\n')
q=json.dumps
save('clash-verge.yaml',f'''mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
find-process-mode: strict
log-level: warning
ipv6: false
proxies:
  - name: Joysol-ESA
    type: vless
    server: {q(p['server'])}
    port: 443
    uuid: {q(p['uuid'])}
    udp: true
    tls: true
    servername: {q(p['server'])}
    client-fingerprint: chrome
    network: ws
    ws-opts:
      path: {q(p['path'])}
      headers:
        Host: {q(p['server'])}
proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - Joysol-ESA
rules:
{chr(10).join('  - '+rule for rule in clash_rules())}
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
{chr(10).join('  - '+rule for rule in CN_CLASH_RULES)}
  - MATCH,PROXY
''')
save('README.txt','''节点：Joysol-ESA，proxy.example.com:443，VLESS + WebSocket + TLS。
Clash Verge Rev / Mihomo：本地导入 clash-verge.yaml 并启用。
其他支持该协议的客户端：复制 vless-esa.txt，从剪贴板导入。
sing-box：使用 sing-box-client.json，本地 HTTP/SOCKS 混合端口 7890。
链路：客户端 → 阿里云 ESA → VPS HTTPS 8443 → 本机 WebSocket 入站。
OpenAI、Claude、Google/Gemini 经家宽 198.51.100.44；其他流量 VPS 203.0.113.10 直出。
规则：国内站点/IP、私网、UU远程直连，国外及未分类流量走代理。
原 Reality 节点仍可使用。此包含节点访问凭据，不含 SSH、Decodo 密码或服务器私钥。
''')
fd=os.open(out/'proxy-esa-configs.zip',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'wb') as f:
 os.fchmod(f.fileno(),0o600)
 with zipfile.ZipFile(f,'w',zipfile.ZIP_DEFLATED) as z:
  for name in ['vless-esa.txt','clash-verge.yaml','sing-box-client.json','README.txt']:z.write(out/name,name)
print('ESA configurations exported privately.')
