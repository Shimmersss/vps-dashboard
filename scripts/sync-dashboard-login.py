"""Apply private dashboard-login.txt credentials without displaying or logging them."""
import hashlib,http.cookiejar,json,secrets,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from remote import connect,m,ROOT
import siteprofile
source=ROOT/'exports/dashboard-login.txt';access_path=ROOT/'exports/dashboard-access.json'
values={}
for line in source.read_text(encoding='utf-8-sig').splitlines():
 for separator in ('：',':'):
  key,found,value=line.partition(separator)
  if found and key.strip() in ('用户名','密码'):
   values[key.strip()]=value;break
username=values.get('用户名','');password=values.get('密码','')
if not username or not password:raise ValueError('用户名或密码字段缺失')
if not username.isascii():raise ValueError('当前登录实现要求 ASCII 用户名；未修改服务器')
access=json.loads(access_path.read_text());profile=siteprofile.load();origin=profile['origin'];url=profile['panel_url']
def browser():return urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def login(opener,user,pw):
 return opener.open(urllib.request.Request(url+'login',data=urllib.parse.urlencode({'username':user,'password':pw,'remember':'on'}).encode(),headers={'Origin':origin,'Content-Type':'application/x-www-form-urlencoded'}),timeout=25)
old=browser();login(old,access['username'],access['password']).read()
client,_=connect();stamp=time.strftime('%Y%m%d%H%M%S');backup='/etc/joysol-dashboard/config.json.credentials-'+stamp+'.bak'
def run(command):
 r=m.execute(client,command,90)
 if r['rc']:raise RuntimeError('远端凭据更新步骤失败：'+r['stderr'][-400:])
 return r
changed=False
try:
 s=client.open_sftp()
 with s.open('/etc/joysol-dashboard/config.json') as f:cfg=json.load(f)
 assert cfg['public_origin']==origin and cfg['esa_gate_enabled'],'Unexpected target configuration'
 salt=secrets.token_hex(16)
 cfg.update(username=username,salt=salt,password_hash=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex())
 candidate='/etc/joysol-dashboard/config.json.credentials-candidate'
 with s.open(candidate,'w') as f:f.write(json.dumps(cfg))
 s.chmod(candidate,0o600);s.close()
 run("python3 - <<'REMOTE'\nimport json\nc=json.load(open('/etc/joysol-dashboard/config.json.credentials-candidate'))\nassert c['username'] and len(c['password_hash'])==64 and len(bytes.fromhex(c['salt']))==16\nREMOTE")
 run('cp -a /etc/joysol-dashboard/config.json '+backup)
 changed=True
 run("set -e\nsystemctl stop joysol-dashboard.service\npython3 - <<'REMOTE'\nimport os,sqlite3\nwith sqlite3.connect('/var/lib/joysol-dashboard/stats.sqlite3') as c:c.execute('DELETE FROM sessions')\nos.replace('/etc/joysol-dashboard/config.json.credentials-candidate','/etc/joysol-dashboard/config.json')\nREMOTE\nsystemctl start joysol-dashboard.service")
 new=browser()
 for attempt in range(6):
  try:
   login(new,username,password).read();break
  except (urllib.error.URLError,TimeoutError):
   if attempt==5:raise
   time.sleep(2)
 d=json.load(new.open(url+'api/data',timeout=25));assert d['health']['connected'] and d['esa_gate_enabled'] and d['esa_origin_verified']
 try:old.open(url+'api/data',timeout=25);raise AssertionError('旧会话未失效')
 except urllib.error.HTTPError as exc:assert exc.code==401
 if (username,password)!=(access['username'],access['password']):
  try:login(browser(),access['username'],access['password']);raise AssertionError('旧账号密码仍被接受')
  except urllib.error.HTTPError as exc:assert exc.code==401
 access.update(username=username,password=password,salt=salt)
 access_path.write_text(json.dumps(access,indent=2)+'\n');access_path.chmod(0o600);source.chmod(0o600)
 # Revoke the verification session as well; no browser session is retained by this script.
 new.open(urllib.request.Request(url+'logout',data=b'{}',headers={'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':d['csrf']}),timeout=25).read()
 proof={'new_credentials_login':True,'old_credentials_rejected':True,'old_sessions_revoked':True,'telemetry_connected':True,'esa_gate_enabled':True,'esa_origin_verified':True,'proxy_restarted':False}
 out=ROOT/'verification/dashboard/credential-sync.json';out.write_text(json.dumps(proof,indent=2)+'\n');out.chmod(0o600)
 print(json.dumps(proof,indent=2))
except Exception:
 if changed:run('cp -a '+backup+' /etc/joysol-dashboard/config.json; systemctl restart joysol-dashboard.service')
 raise
finally:client.close()
