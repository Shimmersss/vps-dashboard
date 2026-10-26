"""Add two ESA variants; credentials remain only in root-owned remote files."""
import base64,copy,json,os,re,secrets,subprocess,time,uuid
from pathlib import Path
from urllib.parse import urlencode,quote
from remote import connect,m,ROOT
out=ROOT/'exports/oxylabs';out.mkdir(mode=0o700,exist_ok=True)
state=out/'subscription-links.json'
links=json.loads(state.read_text()) if state.exists() else {}
client,_=connect();stamp=time.strftime('%Y%m%d%H%M%S')
def run(cmd):
 r=m.execute(client,cmd,90)
 if r['rc']:raise RuntimeError('Remote step failed (details suppressed to protect configuration)')
 return r['stdout']
def save(p,data):
 p.write_bytes(data if isinstance(data,bytes) else data.encode());p.chmod(0o600)
try:
 s=client.open_sftp()
 with s.open('/etc/sing-box/config.json') as f:config=json.load(f)
 with s.open('/etc/nginx/conf.d/joysol-proxy.conf') as f:ng=f.read().decode()
 with s.open('/root/joysol-oxylabs-upstream.json') as f:up=json.load(f)
 try:
  with s.open('/root/joysol-esa-oxylabs-clients.json') as f:meta=json.load(f)
 except IOError:meta={}
 original=next(i for i in config['inbounds'] if i.get('tag')=='vless-esa-ws-in')
 oldpath=original['transport']['path'];match=re.search(r'    location = '+re.escape(oldpath)+r' \{.*?\n    \}',ng,re.S);assert match,'Original protected WS location missing'
 ws_template=match.group();assert 'auth_request /_joysol_ws_gate;' in ws_template
 base_rules=[r for r in config['route']['rules'] if not any(x.startswith('vless-esa-oxy-') for x in r.get('inbound',[]))]
 overrides=[];locations='';files={}
 # Read only the existing public client template, never upstream credentials.
 template=json.loads(subprocess.run(['python3','-c','import yaml,json,sys;print(json.dumps(yaml.safe_load(sys.stdin)))'],input=(ROOT/'exports/esa/clash-verge.yaml').read_text(),capture_output=True,text=True,check=True).stdout)
 for port,expected,listen in [(8001,'198.51.100.146',10001),(8002,'198.51.100.94',10002)]:
  key=str(port);tag='vless-esa-oxy-'+key;exit_tag='oxylabs-'+key
  if key not in meta:meta[key]={'uuid':str(uuid.uuid4()),'path':'/connect/'+secrets.token_urlsafe(24),'expected_ip':expected}
  p=meta[key];name='Joysol-ESA-Oxy-'+key
  config['outbounds']=[o for o in config['outbounds'] if o.get('tag')!=exit_tag]+[{'type':'socks','tag':exit_tag,'server':up['server'],'server_port':port,'version':'5','username':up['username'],'password':up['password']}]
  inbound=copy.deepcopy(original);inbound.update(tag=tag,listen_port=listen);inbound['users']=[{'name':name,'uuid':p['uuid']}];inbound['transport']['path']=p['path']
  config['inbounds']=[i for i in config['inbounds'] if i.get('tag')!=tag]+[inbound]
  for rule in base_rules:
   if rule.get('outbound')=='decodo-us-10003':
    r=copy.deepcopy(rule);r.update(inbound=[tag],outbound=exit_tag);overrides.append(r)
  block=ws_template.replace(oldpath,p['path']).replace('127.0.0.1:10000','127.0.0.1:'+str(listen));locations+=block+'\n'
  variant=copy.deepcopy(template);proxy=variant['proxies'][0];proxy.update(name=name,uuid=p['uuid']);proxy['ws-opts']['path']=p['path'];variant['proxy-groups'][0]['proxies']=[name]
  params=urlencode({'encryption':'none','security':'tls','sni':'proxy.example.com','fp':'chrome','type':'ws','host':'proxy.example.com','path':p['path']})
  uri='vless://'+p['uuid']+'@proxy.example.com:443?'+params+'#'+quote(name)+'\n'
  files['oxy-'+key+'-clash.yaml']=json.dumps(variant,indent=2)+'\n'
  files['oxy-'+key+'-nodes.txt']=base64.b64encode(uri.encode()).decode()+'\n'
  if key not in links:
   token=secrets.token_urlsafe(32);base='https://proxy.example.com/subscription/'+token
   links[key]={'expected_ip':expected,'clash':base+'/clash.yaml','shadowrocket':base+'/nodes.txt'}
  for kind,suffix in [('clash','clash.yaml'),('shadowrocket','nodes.txt')]:
   endpoint=links[key][kind].split('proxy.example.com',1)[1];filename='oxy-'+key+'-'+suffix
   locations+='    location = '+endpoint+' {\n        alias /var/lib/joysol-subscriptions/'+filename+';\n        default_type application/octet-stream;\n        add_header Cache-Control "private, no-store, no-cache, max-age=0" always;\n        add_header profile-update-interval "24" always;\n        access_log off;\n    }\n'
 # Keep private rejection and sniff before route selection; shared UDP reject is unchanged.
 insert=next((i for i,r in enumerate(base_rules) if r.get('outbound')=='decodo-us-10003'),len(base_rules))
 config['route']['rules']=base_rules[:insert]+overrides+base_rules[insert:]
 begin='    # JOYSOL OXYLABS BEGIN\n';end='    # JOYSOL OXYLABS END\n'
 if begin in ng:ng=ng[:ng.index(begin)]+ng[ng.index(end)+len(end):]
 pos=ng.index('    location / { return 404; }',ng.index('listen 8443'))
 ng=ng[:pos]+begin+locations+end+ng[pos:]
 run('set -e; install -d -m 700 /root/joysol-oxylabs-backup-'+stamp+'; cp -a /etc/sing-box/config.json /root/joysol-oxylabs-backup-'+stamp+'/config.json; cp -a /etc/nginx/conf.d/joysol-proxy.conf /root/joysol-oxylabs-backup-'+stamp+'/nginx.conf')
 with s.open('/etc/sing-box/config.json.oxylabs-candidate','w') as f:f.write(json.dumps(config,indent=2)+'\n')
 s.chmod('/etc/sing-box/config.json.oxylabs-candidate',0o600)
 run('/usr/local/bin/sing-box check -c /etc/sing-box/config.json.oxylabs-candidate')
 with s.open('/root/joysol-esa-oxylabs-clients.json','w') as f:f.write(json.dumps(meta))
 s.chmod('/root/joysol-esa-oxylabs-clients.json',0o600)
 for name,data in files.items():
  save(out/name,data)
  with s.open('/var/lib/joysol-subscriptions/'+name,'w') as f:f.write(data)
  s.chmod('/var/lib/joysol-subscriptions/'+name,0o640);s.chown('/var/lib/joysol-subscriptions/'+name,0,33)
 with s.open('/etc/nginx/conf.d/joysol-proxy.conf','w') as f:f.write(ng)
 s.close()
 try:run('nginx -t')
 except Exception:
  run('cp -a /root/joysol-oxylabs-backup-'+stamp+'/nginx.conf /etc/nginx/conf.d/joysol-proxy.conf');raise
 save(state,json.dumps(links,indent=2)+'\n')
 run('set -e; if ! cmp -s /etc/sing-box/config.json.oxylabs-candidate /etc/sing-box/config.json; then cp /etc/sing-box/config.json.oxylabs-candidate /etc/sing-box/config.json; systemctl restart sing-box; fi; systemctl reload nginx')
 save(state,json.dumps(links,indent=2)+'\n')
 print('Two independent ESA variants deployed. Four subscription links saved privately.')
finally:client.close()
