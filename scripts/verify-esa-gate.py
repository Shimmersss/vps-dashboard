"""Real WebSocket/VLESS traffic and origin bypass checks; never log node secrets."""
import base64,ipaddress,json,os,socket,ssl,struct,time,uuid
from pathlib import Path
from remote import connect,m
import siteprofile
profile=siteprofile.load();ORIGIN_IP=siteprofile.ssh_host()
ROOT=Path(__file__).resolve().parents[1]
a=json.loads((ROOT/'exports/esa/sing-box-client.json').read_text())
x=next(x for x in a['outbounds'] if x['type']=='vless');path=x['transport']['path'];host=profile['host']
def ws(server,port,headers=None,payload=None):
 with socket.create_connection((server,port),timeout=20) as raw:
  with ssl.create_default_context().wrap_socket(raw,server_hostname=host) as s:
   key=base64.b64encode(os.urandom(16)).decode()
   request=f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
   request+=''.join(k+': '+v+'\r\n' for k,v in (headers or {}).items())+'\r\n';s.sendall(request.encode())
   reply=b''
   while not reply.endswith(b'\r\n\r\n'):
    chunk=s.recv(1)
    if not chunk:raise ConnectionError('Handshake closed')
    reply+=chunk
    if len(reply)>16000:raise ValueError('Oversize handshake')
   code=int(reply.split()[1])
   if payload and code==101:
    mask=os.urandom(4);size=len(payload)
    frame=b'\x82'+(bytes([0x80|size]) if size<126 else b'\xfe'+struct.pack('!H',size))+mask+bytes(b^mask[i%4] for i,b in enumerate(payload));s.sendall(frame)
    data=s.recv(8192);assert data,'No VLESS response'
   return code
start=int(time.time()*1000)
# Never provide the origin credential to a public request: ESA must supply it.
target=b'example.net';payload=b'\x00'+uuid.UUID(x['uuid']).bytes+b'\x00\x01'+struct.pack('!H',80)+b'\x02'+bytes([len(target)])+target+b'GET / HTTP/1.1\r\nHost: example.net\r\nConnection: close\r\n\r\n'
proof={}
proof['origin_direct_without_guard']=ws(ORIGIN_IP,profile['origin_tls_port'],{'X-Forwarded-For':'8.8.8.8'})
assert proof['origin_direct_without_guard']==403
for name,headers in [('normal',{}),('forged_guard',{'X-Joysol-Origin':'untrusted-client-value'}),('forged_xff',{'X-Forwarded-For':'8.8.8.8, 9.9.9.9'})]:
 proof[name]=ws(host,443,headers,payload);assert proof[name]==101,name
# TUN routing can send an IP-check website through the node itself. Compare the
# normal ESA request with both forged-header cases on this same destination.
with connect()[0] as c:
 for _ in range(8):
  command="python3 - <<'REMOTE'\nimport sqlite3,json\nc=sqlite3.connect('/var/lib/joysol-dashboard/stats.sqlite3')\nprint(json.dumps([x[0] for x in c.execute(\"select ip from connections where domain='example.net' and started>=?\",("+str(start)+",))]))\nREMOTE"
  r=m.execute(c,command,20);assert r['rc']==0
  sources=json.loads(r['stdout'])
  if len(sources)>=3:break
  time.sleep(1)
 assert len(sources)>=3 and len(set(sources))==1 and ipaddress.ip_address(sources[0]).is_global and sources[0] not in ('8.8.8.8','9.9.9.9',ORIGIN_IP),'Forged requests changed the persisted source'
 proof['persisted_real_ip_for_all_three']=True
out=ROOT/'verification/dashboard/esa-gate-check.json';out.write_text(json.dumps(proof,indent=2)+'\n');out.chmod(0o600)
print(json.dumps(proof,indent=2))
