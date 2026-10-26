from client_rules import clash_rules,sing_rules,china_split,CN_CLASH_RULES
"""Export client credentials over verified SSH; never print secrets."""
import json,os,sys,zipfile
from pathlib import Path
from urllib.parse import urlencode,quote
from remote import connect,m,ROOT
out=ROOT/'exports'
out.mkdir(mode=0o700,exist_ok=True);out.chmod(0o700)
def save(name,content):
 path=out/name
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:
  os.fchmod(f.fileno(),0o600);f.write(content)
with connect()[0] as c:
 with c.open_sftp() as sftp:
  with sftp.open('/root/joysol-proxy-client.json') as f:p=json.load(f)
settings=m.load_settings(ROOT/'.env');host=settings['SSH_HOST']
query=urlencode({'encryption':'none','security':'reality','sni':p['server_name'],'fp':'chrome','pbk':p['public_key'],'sid':p['short_id'],'type':'tcp','flow':'xtls-rprx-vision'})
uri=f"vless://{p['uuid']}@{host}:{p['port']}?{query}#"+quote('Joysol-Reality')
save('vless-reality.txt',uri+'\n')
proxy={'type':'vless','tag':'proxy','server':host,'server_port':p['port'],'uuid':p['uuid'],'flow':'xtls-rprx-vision','tls':{'enabled':True,'server_name':p['server_name'],'utls':{'enabled':True,'fingerprint':'chrome'},'reality':{'enabled':True,'public_key':p['public_key'],'short_id':p['short_id']}}}
client={'log':{'level':'warn'},'inbounds':[{'type':'mixed','tag':'mixed-in','listen':'127.0.0.1','listen_port':7890}],'outbounds':[proxy,{'type':'direct','tag':'direct'}],'route':{'rules':sing_rules()+[{'ip_is_private':True,'action':'route','outbound':'direct'}],'final':'proxy'}}
save('sing-box-client.json',json.dumps(china_split(client,'sing'),indent=2)+'\n')
q=json.dumps
clash=f'''mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
find-process-mode: strict
log-level: warning
ipv6: false
proxies:
  - name: Joysol-Reality
    type: vless
    server: {q(host)}
    port: {p['port']}
    uuid: {q(p['uuid'])}
    flow: xtls-rprx-vision
    network: tcp
    udp: true
    tls: true
    servername: {q(p['server_name'])}
    client-fingerprint: chrome
    reality-opts:
      public-key: {q(p['public_key'])}
      short-id: {q(p['short_id'])}
proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - Joysol-Reality
rules:
{chr(10).join('  - '+rule for rule in clash_rules())}
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
{chr(10).join('  - '+rule for rule in CN_CLASH_RULES)}
  - MATCH,PROXY
'''
save('clash-verge.yaml',clash)
save('README.txt','''代理服务：VLESS + Reality，TCP 443。
Clash Verge Rev / Mihomo：从本地文件导入 clash-verge.yaml，启用该配置。
v2rayN / v2rayNG / 支持 VLESS Reality 的客户端：复制 vless-reality.txt 全部内容，从剪贴板导入。
sing-box：导入 sing-box-client.json；它提供 127.0.0.1:7890 的 HTTP/SOCKS 混合代理，不自动启用系统代理或 TUN。
规则：私网、UU远程及国内站点/IP直连，国外与未分类流量通过 VPS；VPS 端将 OpenAI、Claude、Google/Gemini 转至 Decodo 家宽 198.51.100.44，其余流量由 VPS 直出。
这些文件包含节点访问凭据，请私密保存。无 SSH 密码或服务器私钥。
''')
archive=out/'proxy-configs.zip'
fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'wb') as f:
 os.fchmod(f.fileno(),0o600)
 with zipfile.ZipFile(f,'w',zipfile.ZIP_DEFLATED) as z:
  for name in ['vless-reality.txt','clash-verge.yaml','sing-box-client.json','README.txt']:z.write(out/name,name)
print('Exported 3 client formats and proxy-configs.zip; credentials omitted from console.')
