"""Verify live client routing via real TLS sessions and controller observations."""
import json,socket,ssl,subprocess,urllib.request
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
r=Path.home()/'Library/Application Support/io.github.clash-verge-rev.clash-verge-rev'
cfg=yaml.safe_load((r/'clash-verge.yaml').read_text());port=cfg.get('mixed-port') or cfg['port']
def api(path):return json.loads(subprocess.check_output(['curl','-sS','--unix-socket','/tmp/verge/verge-mihomo.sock','http://localhost/'+path]))
proof={}
for host,expected in [('www.bilibili.com','DIRECT'),('api.bilibili.com','DIRECT'),('www.baidu.com','DIRECT'),('github.com','PROXY'),('chatgpt.com','PROXY')]:
 with socket.create_connection(('127.0.0.1',port),timeout=25) as raw:
  source_port=str(raw.getsockname()[1])
  raw.sendall(('CONNECT '+host+':443 HTTP/1.1\r\nHost: '+host+':443\r\n\r\n').encode());reply=b''
  while not reply.endswith(b'\r\n\r\n'):
   b=raw.recv(1)
   if not b:raise ConnectionError('CONNECT failed')
   reply+=b
  assert b'200' in reply.split(b'\r\n')[0]
  with ssl.create_default_context().wrap_socket(raw,server_hostname=host) as tls:
   connections=[c for c in api('connections')['connections'] if c['metadata'].get('host')==host and str(c['metadata'].get('sourcePort'))==source_port]
   assert connections,'No live connection for '+host
   candidates=[c for c in connections if ('DIRECT' in c['chains'])==(expected=='DIRECT')]
   assert candidates,'Unexpected route for '+host
   row=candidates[-1];proof[host]={'expected':expected,'chains':row['chains'],'rule':row['rule'],'rulePayload':row['rulePayload'],'tls':tls.version()}
links=json.loads((ROOT/'exports/subscription-links.json').read_text());op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for key,file in [('clash','exports/esa/clash-verge.yaml'),('sing_box','exports/esa/sing-box-client.json')]:assert op.open(links[key],timeout=30).read()==(ROOT/file).read_bytes()
proof['published_matches_local']=True
p=ROOT/'verification/china-split-check.json';p.write_text(json.dumps(proof,indent=2)+'\n');p.chmod(0o600);print(json.dumps(proof,indent=2))
