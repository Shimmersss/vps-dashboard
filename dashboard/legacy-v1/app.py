"""Joysol node dashboard: streamed lifecycle telemetry, persistent accounting, web login."""
import collections,hashlib,hmac,ipaddress,json,logging,os,secrets,sqlite3,subprocess,threading,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import grpc,maxminddb
from flask import Flask,request,jsonify,make_response,redirect,send_from_directory
import telemetry_pb2 as pb

ROOT=Path(__file__).resolve().parent
CONFIG=json.loads(Path(os.environ.get('JOYSOL_DASHBOARD_CONFIG','/etc/joysol-dashboard/config.json')).read_text())
DATA=Path(CONFIG.get('data_dir','/var/lib/joysol-dashboard'));DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'stats.sqlite3'
lock=threading.RLock();stop=threading.Event();health={'connected':False,'last_event':0,'last_error':'等待内核事件','started':time.time()}
channel=grpc.insecure_channel(CONFIG.get('api_address','127.0.0.1:8306'))
subscribe=channel.unary_stream('/daemon.StartedService/SubscribeConnections',request_serializer=pb.SubscribeConnectionsRequest.SerializeToString,response_deserializer=pb.ConnectionEvents.FromString)
close_rpc=channel.unary_unary('/daemon.StartedService/CloseConnection',request_serializer=pb.CloseConnectionRequest.SerializeToString,response_deserializer=pb.Empty.FromString)
metadata=[('authorization','Bearer '+CONFIG['api_secret'])]
app=Flask(__name__);app.config.update(MAX_CONTENT_LENGTH=8192)
logger=logging.getLogger(__name__)

def db():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=20000');return c

def init():
 with db() as c:
  c.executescript('''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY,ip TEXT NOT NULL,domain TEXT NOT NULL,protocol TEXT NOT NULL,inbound TEXT NOT NULL,outbound TEXT NOT NULL,started INTEGER NOT NULL,ended INTEGER DEFAULT 0,upload INTEGER DEFAULT 0,download INTEGER DEFAULT 0,seen INTEGER NOT NULL,active INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS by_ip ON connections(ip);
CREATE INDEX IF NOT EXISTS by_group ON connections(ip,domain,protocol,inbound);
CREATE TABLE IF NOT EXISTS daily(day TEXT,ip TEXT,upload INTEGER DEFAULT 0,download INTEGER DEFAULT 0,PRIMARY KEY(day,ip));
CREATE TABLE IF NOT EXISTS bans(ip TEXT PRIMARY KEY,reason TEXT,created INTEGER,enforcement TEXT);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,csrf TEXT,expires INTEGER);
CREATE TABLE IF NOT EXISTS failures(ip TEXT,at INTEGER);
CREATE INDEX IF NOT EXISTS failure_time ON failures(at);
CREATE TABLE IF NOT EXISTS audit(at INTEGER,action TEXT,ip TEXT,detail TEXT);
''')
  c.execute("INSERT OR IGNORE INTO meta VALUES('tracking_since',?)",(str(int(time.time()*1000)),))
  c.execute('UPDATE connections SET active=0 WHERE active=1')
 os.chmod(DB,0o600)

city=None;asn=None

def geo(ip):
 try:
  a=ipaddress.ip_address(ip)
  if not a.is_global:return {'location':'私网 / 本机','isp':'本地网络','asn':''}
  g=city.get(ip) or {} if city else {};n=asn.get(ip) or {} if asn else {}
  parts=[]
  for part in [g.get('country',{}),*(g.get('subdivisions',[])[:1]),g.get('city',{})]:
   names=part.get('names',{});v=names.get('zh-CN') or names.get('en')
   if v and v not in parts:parts.append(v)
  return {'location':' · '.join(parts) or '未知','isp':n.get('autonomous_system_organization','未知'),'asn':n.get('autonomous_system_number','')}
 except (ValueError,TypeError):return {'location':'未知','isp':'未知','asn':''}

def source_ip(source):
 try:return str(ipaddress.ip_address(source))
 except ValueError:pass
 try:return str(ipaddress.ip_address(source.rsplit(':',1)[0].strip('[]')))
 except ValueError:return 'unknown'

def ingest(batch):
 now=int(time.time()*1000)
 with lock,db() as c:
  if batch.reset:c.execute('UPDATE connections SET active=0 WHERE active=1')
  for e in batch.events:
   row=c.execute('SELECT * FROM connections WHERE id=?',(e.id,)).fetchone()
   if e.HasField('connection'):
    x=e.connection;up=max(0,x.uplinkTotal);down=max(0,x.downlinkTotal)
    ip=source_ip(x.source);domain=(x.domain or x.destination or '未知').lower().rstrip('.')
    protocol='/'.join(filter(None,[x.network,x.protocol])) or '未知'
    inbound=(x.inboundType+'/'+x.inbound).strip('/')
    end=x.closedAt or e.closedAt
    # Upsert cumulative counters rather than deltas: reconnect/replay must not double count.
    before_up=row['upload'] if row else 0;before_down=row['download'] if row else 0
    up=max(up,before_up);down=max(down,before_down)
    c.execute('''INSERT INTO connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET domain=excluded.domain,protocol=excluded.protocol,outbound=excluded.outbound,ended=excluded.ended,upload=excluded.upload,download=excluded.download,seen=excluded.seen,active=excluded.active''',(e.id,ip,domain,protocol,inbound,x.outbound,x.createdAt,end,up,down,now,0 if end or e.type==2 else 1))
   elif row:
    ip=row['ip'];before_up=row['upload'];before_down=row['download']
    up=before_up+max(0,e.uplinkDelta);down=before_down+max(0,e.downlinkDelta)
    c.execute('UPDATE connections SET upload=?,download=?,ended=?,active=?,seen=? WHERE id=?',(up,down,e.closedAt,0 if e.type==2 else 1,now,e.id))
   else:continue
   du=max(0,up-before_up);dd=max(0,down-before_down)
   if du or dd:
    day=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
    c.execute('INSERT INTO daily VALUES(?,?,?,?) ON CONFLICT(day,ip) DO UPDATE SET upload=upload+excluded.upload,download=download+excluded.download',(day,ip,du,dd))
   if c.execute('SELECT 1 FROM bans WHERE ip=?',(ip,)).fetchone() and not (e.type==2):
    threading.Thread(target=close_connection,args=(e.id,),daemon=True).start()
 health.update(connected=True,last_event=now,last_error='')

def close_connection(cid):
 try:close_rpc(pb.CloseConnectionRequest(id=cid),metadata=metadata,timeout=5)
 except grpc.RpcError:pass

def collect():
 while not stop.is_set():
  try:
   for batch in subscribe(pb.SubscribeConnectionsRequest(interval=1_000_000_000),metadata=metadata):
    ingest(batch)
  except Exception as exc:
   health.update(connected=False,last_error=type(exc).__name__)
   logger.warning('Telemetry disconnected: %s',type(exc).__name__)
   with db() as c:c.execute('UPDATE connections SET active=0 WHERE active=1')
   stop.wait(2)

def canonical_ip(value):
 ip=ipaddress.ip_address(value)
 if not ip.is_global:raise ValueError('只允许封禁公网单个 IP')
 return str(ip)

def firewall(ip,ban):
 ip=canonical_ip(ip)
 binary='/usr/sbin/ip6tables' if ':' in ip else '/usr/sbin/iptables'
 def run(args,check=True):return subprocess.run([binary,'-w','5',*args],capture_output=True,check=check,timeout=10)
 run(['-N','JOYSOL_NODE_BANS'],False)
 jump=['INPUT','-p','tcp','--dport','443','-j','JOYSOL_NODE_BANS']
 if run(['-C',*jump],False).returncode:run(['-I','INPUT','1',*jump[1:]])
 rule=['JOYSOL_NODE_BANS','-p','tcp','-s',ip,'-j','REJECT','--reject-with','tcp-reset']
 exists=run(['-C',*rule],False).returncode==0
 if ban and not exists:run(['-A',*rule])
 if not ban and exists:run(['-D',*rule])

def enforce_existing():
 with db() as c:rows=c.execute('SELECT ip FROM bans').fetchall()
 for row in rows:
  try:firewall(row['ip'],True)
  except Exception:logger.error('Ban reapply failed')

def session():
 token=request.cookies.get('__Host-joysol_session','')
 if not token:return None
 with db() as c:return c.execute('SELECT csrf,expires FROM sessions WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()

def request_ip():
 # Nginx overwrites this header; backend listens only on loopback.
 return request.headers.get('X-Dashboard-Client-IP',request.remote_addr)

def gate_ip():
 # Origin guard must match the secret configured in ESA. Untrusted direct origins
 # must not be allowed to impersonate arbitrary forwarded client IPs.
 guard=request.headers.get('X-Origin-Guard','')
 if not CONFIG.get('origin_guard') or not hmac.compare_digest(guard,CONFIG['origin_guard']):return None
 raw=request.headers.get('X-ESA-Client-IP','').strip()
 if not raw:
  # ESA appends the actual client to XFF; use the last address, never the first.
  raw=request.headers.get('X-Origin-XFF','').split(',')[-1].strip()
 try:return str(ipaddress.ip_address(raw))
 except ValueError:return None

@app.after_request
def security(response):
 response.headers['Cache-Control']='private, no-store, no-cache, max-age=0'
 response.headers['X-Content-Type-Options']='nosniff'
 response.headers['X-Frame-Options']='DENY'
 response.headers['Referrer-Policy']='same-origin'
 response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
 return response

@app.before_request
def protect():
 if request.path.startswith('/internal/'):
  if request.remote_addr!='127.0.0.1':return '',403
  return
 if request.path in ('/monitor/login','/monitor/static/style.css','/monitor/static/login.js'):return
 s=session()
 if not s:
  if request.path.startswith('/monitor/api/'):return jsonify(error='请重新登录'),401
  return redirect('/monitor/login')
 if request.method not in ('GET','HEAD','OPTIONS'):
  if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),s['csrf']):return jsonify(error='请求验证失败'),403
  if request.headers.get('Origin')!=CONFIG['public_origin']:return jsonify(error='来源验证失败'),403

@app.route('/internal/gate')
def gate():
 ip=gate_ip()
 if not ip:return '',403
 with db() as c:blocked=c.execute('SELECT 1 FROM bans WHERE ip=?',(ip,)).fetchone()
 r=make_response('',403 if blocked else 204);r.headers['X-Verified-Client-IP']=ip
 return r

@app.route('/monitor/login',methods=['GET','POST'])
def login():
 def failure(message,status):
  return make_response((ROOT/'static/login.html').read_text().replace('<p id="error" role="alert"></p>','<p id="error" role="alert">'+message+'</p>'),status)
 if request.method=='GET':return send_from_directory(ROOT/'static','login.html')
 if request.headers.get('Origin')!=CONFIG['public_origin']:return jsonify(error='来源验证失败'),403
 ip=request_ip();now=int(time.time())
 with lock,db() as c:
  c.execute('DELETE FROM failures WHERE at<?',(now-900,));c.execute('DELETE FROM sessions WHERE expires<?',(now,))
  count=c.execute('SELECT COUNT(*) FROM failures WHERE ip=?',(ip,)).fetchone()[0]
  total=c.execute('SELECT COUNT(*) FROM failures').fetchone()[0]
  if count>=10 or total>=100:return failure('尝试过多，请 15 分钟后重试。',429)
  valid_user=hmac.compare_digest(request.form.get('username',''),CONFIG['username'])
  digest=hashlib.pbkdf2_hmac('sha256',request.form.get('password','').encode(),bytes.fromhex(CONFIG['salt']),600000).hex()
  if not valid_user or not hmac.compare_digest(digest,CONFIG['password_hash']):
   c.execute('INSERT INTO failures VALUES(?,?)',(ip,now));return failure('用户名或密码错误，请重试。',401)
  token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32)
  days=30 if request.form.get('remember')=='on' else 1
  c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),csrf,now+days*86400))
  c.execute('INSERT INTO audit VALUES(?,?,?,?)',(now,'login',ip,''))
 r=redirect('/monitor/');r.set_cookie('__Host-joysol_session',token,max_age=days*86400,secure=True,httponly=True,samesite='Strict',path='/')
 return r

@app.route('/monitor/logout',methods=['POST'])
def logout():
 with db() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get('__Host-joysol_session','').encode()).hexdigest(),))
 r=jsonify(ok=True);r.delete_cookie('__Host-joysol_session',secure=True,httponly=True,samesite='Strict',path='/');return r

@app.route('/monitor/')
def index():return send_from_directory(ROOT/'static','index.html')
@app.route('/monitor/static/<path:name>')
def static_asset(name):return send_from_directory(ROOT/'static',name)

@app.route('/monitor/api/data')
def data():
 since=request.args.get('since','all');where='';args=[]
 # User totals remain all-time; history may be filtered by a bounded relative window.
 cutoff={'24h':86400,'7d':604800,'30d':2592000}.get(since)
 if cutoff:where=' WHERE seen>=?';args=[int((time.time()-cutoff)*1000)]
 with db() as c:
  ips=[dict(r) for r in c.execute('''SELECT ip,COUNT(*) connections,SUM(active) online,SUM(upload) upload,SUM(download) download,MAX(seen) last_seen,MIN(started) first_seen FROM connections GROUP BY ip''')]
  bans={r['ip']:dict(r) for r in c.execute('SELECT * FROM bans')}
  for ip in bans:
   if not any(x['ip']==ip for x in ips):ips.append({'ip':ip,'connections':0,'online':0,'upload':0,'download':0,'last_seen':0,'first_seen':0})
  for row in ips:row.update(geo(row['ip']),banned=row['ip'] in bans,ban_reason=bans.get(row['ip'],{}).get('reason',''))
  ip=request.args.get('ip','')
  if ip:
   if where:where+=' AND ip=?'
   else:where=' WHERE ip=?'
   args.append(ip)
  # Aggregate exactly by IP + destination domain + protocol + inbound, even if outbound changes.
  groups=[dict(r) for r in c.execute('''SELECT ip,domain,protocol,inbound,COUNT(*) connections,SUM(active) online,SUM(upload) upload,SUM(download) download,MIN(started) first_seen,MAX(seen) last_seen,GROUP_CONCAT(DISTINCT outbound) outbound FROM connections'''+where+' GROUP BY ip,domain,protocol,inbound ORDER BY last_seen DESC LIMIT 2000',args)]
  daily=[dict(r) for r in c.execute('SELECT day,SUM(upload) upload,SUM(download) download FROM daily GROUP BY day ORDER BY day DESC LIMIT 30')]
  daily_ips=[dict(r) for r in c.execute('SELECT day,ip,upload,download FROM daily WHERE day IN (SELECT DISTINCT day FROM daily ORDER BY day DESC LIMIT 30) ORDER BY day DESC,upload+download DESC')]
  for row in daily_ips:row.update(geo(row['ip']))
  since_value=c.execute("SELECT value FROM meta WHERE key='tracking_since'").fetchone()[0]
 return jsonify(esa_gate_enabled=CONFIG.get('esa_gate_enabled',False),esa_origin_verified=hmac.compare_digest(request.headers.get('X-Joysol-Origin',''),CONFIG['origin_guard']),ips=ips,history=groups,daily=daily,daily_ips=daily_ips,bans=list(bans.values()),health=health,tracking_since=int(since_value),csrf=session()['csrf'],history_limit=2000)

@app.route('/monitor/api/ban',methods=['POST'])
def ban():
 body=request.get_json(silent=True) or {}
 try:ip=canonical_ip(body.get('ip',''))
 except ValueError:return jsonify(error='请输入单个公网 IPv4 或 IPv6 地址'),400
 action=body.get('action');reason=str(body.get('reason',''))[:160]
 if action not in ('ban','unban'):return jsonify(error='无效操作'),400
 try:
  with lock:
   firewall(ip,action=='ban')
   with db() as c:
    if action=='ban':c.execute('INSERT OR REPLACE INTO bans VALUES(?,?,?,?)',(ip,reason,int(time.time()),'Reality443 + ESA gateway'))
    else:c.execute('DELETE FROM bans WHERE ip=?',(ip,))
    ids=[r['id'] for r in c.execute('SELECT id FROM connections WHERE ip=? AND active=1',(ip,))]
    c.execute('INSERT INTO audit VALUES(?,?,?,?)',(int(time.time()),action,ip,reason))
   if action=='ban':
    for cid in ids:close_connection(cid)
  return jsonify(ok=True,closed=len(ids) if action=='ban' else 0)
 except (subprocess.SubprocessError,OSError):return jsonify(error='封禁执行失败，请检查服务日志'),503

init()
for kind in ('city','asn'):
 p=DATA/(kind+'.mmdb')
 if p.exists():
  if kind=='city':city=maxminddb.open_database(str(p))
  else:asn=maxminddb.open_database(str(p))
if not os.environ.get('JOYSOL_TEST'):
 enforce_existing();threading.Thread(target=collect,daemon=True).start()
