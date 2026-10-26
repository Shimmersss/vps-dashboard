"""Verify HTTPS login/session and live telemetry without printing credentials or client IPs."""
import http.cookiejar,json,os,urllib.request,urllib.parse,urllib.error
from pathlib import Path
from remote import connect,m
import siteprofile
profile=siteprofile.load();ORIGIN=profile['origin'];HOST=profile['host'];PORT=profile['origin_tls_port']
ROOT=Path(__file__).resolve().parents[1]
a=json.loads((ROOT/'exports/dashboard-access.json').read_text());base=a['url'].rstrip('/')
jar=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
def req(path,body=None,headers=None):return opener.open(urllib.request.Request(base+path,data=body,headers=headers or {}),timeout=30)
def signed_gate_probe():
 command=(r"""python3 - <<'REMOTE'
import base64,json,os,socket,ssl
cfg=json.load(open('/etc/joysol-dashboard/config.json'))
meta=json.load(open('/root/joysol-esa-client.json'))
with socket.create_connection(('127.0.0.1',__PORT__),timeout=10) as raw:
 with ssl.create_default_context().wrap_socket(raw,server_hostname='__HOST__') as tls:
  headers={'Host':'__HOST__','Upgrade':'websocket','Connection':'Upgrade','Sec-WebSocket-Version':'13','Sec-WebSocket-Key':base64.b64encode(os.urandom(16)).decode(),'X-Joysol-Origin':cfg['origin_guard'],'X-Forwarded-For':'8.8.8.8'}
  data='GET '+meta['path']+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n'
  tls.sendall(data.encode());print(int(tls.recv(1000).split()[1]))
REMOTE""").replace('__PORT__',str(PORT)).replace('__HOST__',HOST)
 result=m.execute(probe_client,command,25)
 assert result['rc']==0,'Signed gateway probe failed: '+result['stderr'][-500:]
 return int(result['stdout'].strip())
evidence={}
r=req('/login');assert r.status==200 and b'autocomplete="current-password"' in r.read();evidence['login_form']=True
try:req('/api/data');raise AssertionError('Anonymous telemetry exposed')
except urllib.error.HTTPError as e:assert e.code==401;evidence['anonymous_denied']=True
r=req('/login',urllib.parse.urlencode({k:a[k] for k in ['username','password']}|{'remember':'on'}).encode(),{'Origin':ORIGIN,'Content-Type':'application/x-www-form-urlencoded'});assert r.status==200
cookies=[c for c in jar if c.name=='__Host-joysol_session'];assert len(cookies)==1 and cookies[0].secure and cookies[0].has_nonstandard_attr('HttpOnly');evidence['secure_session']=True
with req('/api/data') as r:d=json.load(r)
evidence.update(telemetry_connected=d['health']['connected'],recorded_connections=sum(x['connections'] for x in d['ips']),recorded_bytes=sum(x['upload']+x['download'] for x in d['ips']),geo_rows=sum(x['location']!='未知' and x['isp']!='未知' for x in d['ips']),history_groups=len(d['history']),esa_gate_enabled=d['esa_gate_enabled'],esa_origin_verified=d['esa_origin_verified'])
assert d['health']['connected'],'Telemetry not connected'
try:req('/api/ban',b'{"ip":"8.8.8.8","action":"ban"}',{'Content-Type':'application/json','Origin':ORIGIN});raise AssertionError('Missing CSRF accepted')
except urllib.error.HTTPError as e:assert e.code==403;evidence['csrf_denied']=True
probe_client=connect()[0] if d['esa_gate_enabled'] else None
# Exercise a real, narrow firewall rule; this source is not a current client.
assert not any(x['ip']=='8.8.8.8' for x in d['ips']), 'Test source already used; choose an unused address'
headers={'Content-Type':'application/json','Origin':ORIGIN,'X-CSRF-Token':d['csrf']}
try:
 with req('/api/ban',json.dumps({'ip':'8.8.8.8','action':'ban','reason':'deployment verification'}).encode(),headers) as r:assert json.load(r)['ok']
 with req('/api/data') as r:assert any(x['ip']=='8.8.8.8' for x in json.load(r)['bans'])
 evidence['real_firewall_ban']=True
 if d['esa_gate_enabled']:assert signed_gate_probe()==403;evidence['esa_banned_handshake_denied']=True
finally:
 with req('/api/ban',json.dumps({'ip':'8.8.8.8','action':'unban'}).encode(),headers) as r:assert json.load(r)['ok']
 evidence['real_firewall_unban']=True
 if d['esa_gate_enabled']:assert signed_gate_probe()==101;evidence['esa_unbanned_handshake_accepted']=True
with req('/logout',b'{}',{'Content-Type':'application/json','Origin':ORIGIN,'X-CSRF-Token':d['csrf']}) as r:assert r.status==200
try:req('/api/data');raise AssertionError('Revoked session accepted')
except urllib.error.HTTPError as e:assert e.code==401;evidence['logout_revoked']=True
if probe_client:probe_client.close()
out=ROOT/'verification/dashboard/https-check.json';out.write_text(json.dumps(evidence,indent=2)+'\n');out.chmod(0o600)
print(json.dumps(evidence,indent=2))
