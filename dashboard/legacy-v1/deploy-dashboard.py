"""Deploy the browser dashboard; no secrets are printed. Explicit --gate enables ESA gate."""
import hashlib,importlib.util,json,os,secrets,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remote',ROOT/'scripts/remote.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
private=ROOT/'exports/dashboard-access.json'
if private.exists():access=json.loads(private.read_text())
else:
 access={'url':'https://proxy.example.com/monitor/','username':'admin','password':secrets.token_urlsafe(24),'origin_guard':secrets.token_hex(32),'api_secret':secrets.token_urlsafe(40),'salt':secrets.token_hex(16)}
 private.write_text(json.dumps(access,indent=2)+'\n');private.chmod(0o600)
guardfile=ROOT/'exports/esa-origin-guard.txt';guardfile.write_text(access['origin_guard']+'\n');guardfile.chmod(0o600)
loginfile=ROOT/'exports/dashboard-login.txt';loginfile.write_text('地址：'+access['url']+'\n用户名：'+access['username']+'\n密码：'+access['password']+'\n');loginfile.chmod(0o600)
config={k:access[k] for k in ('username','salt','api_secret','origin_guard')}
config.update(password_hash=hashlib.pbkdf2_hmac('sha256',access['password'].encode(),bytes.fromhex(access['salt']),600000).hex(),public_origin='https://proxy.example.com')
client,_=r.connect()
def run(cmd):
 result=r.m.execute(client,cmd,180)
 if result['rc']:raise RuntimeError('Remote step failed (exit %s): %s' % (result['rc'],result['stderr'][-1800:]))
 print(result['stdout'],end='')
try:
 run('install -d -m 700 /etc/joysol-dashboard /var/lib/joysol-dashboard; install -d -m 755 /opt/joysol-dashboard/static; test -s /etc/sing-box/config.json; nginx -V 2>&1 | sed -n "/auth_request/p" | cut -c1-80')
 s=client.open_sftp()
 with s.open('/etc/nginx/conf.d/joysol-proxy.conf') as f:existing_ng=f.read().decode()
 config['esa_gate_enabled']='--gate' in sys.argv or 'auth_request /_joysol_ws_gate;' in existing_ng
 with s.open('/etc/tmpfiles.d/joysol-dashboard.conf','w') as f:f.write('f /run/xtables.lock 0600 root root -\n')
 for f in (ROOT/'dashboard').rglob('*'):
  if f.is_file() and '__pycache__' not in str(f):s.put(str(f),'/opt/joysol-dashboard/'+str(f.relative_to(ROOT/'dashboard')))
 with s.open('/etc/joysol-dashboard/config.json','w') as f:f.write(json.dumps(config))
 s.chmod('/etc/joysol-dashboard/config.json',0o600)
 unit='''[Unit]
Description=Joysol node statistics and access panel
After=network.target sing-box.service systemd-tmpfiles-setup.service
[Service]
User=root
WorkingDirectory=/opt/joysol-dashboard
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/joysol-dashboard/venv/bin/python -m gunicorn --bind 127.0.0.1:8307 --workers 1 --threads 8 --timeout 60 --access-logfile /dev/null --error-logfile - app:app
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/joysol-dashboard /run/xtables.lock
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
[Install]
WantedBy=multi-user.target
'''
 with s.open('/etc/systemd/system/joysol-dashboard.service','w') as f:f.write(unit)
 s.close()
 run("protoc -I/opt/joysol-dashboard --python_out=/opt/joysol-dashboard /opt/joysol-dashboard/telemetry.proto; touch /run/xtables.lock; python3 -m py_compile /opt/joysol-dashboard/app.py")
 # Read and merge actual current configuration; back up before changing it.
 s=client.open_sftp()
 with s.open('/etc/sing-box/config.json') as f:sb=json.load(f)
 service={'type':'api','tag':'joysol-monitor-api','listen':'127.0.0.1','listen_port':8306,'secret':access['api_secret'],'dashboard':{'enabled':False}}
 sb['services']=[x for x in sb.get('services',[]) if x.get('tag')!='joysol-monitor-api']+[service]
 timestamp=time.strftime('%Y%m%d%H%M%S')
 run('cp -a /etc/sing-box/config.json /etc/sing-box/config.json.dashboard-'+timestamp+'.bak; cp -a /etc/nginx/conf.d/joysol-proxy.conf /etc/nginx/conf.d/joysol-proxy.conf.dashboard-'+timestamp+'.bak')
 with s.open('/etc/sing-box/config.json.dashboard-candidate','w') as f:f.write(json.dumps(sb,indent=2)+'\n')
 s.chmod('/etc/sing-box/config.json.dashboard-candidate',0o600)
 run('/usr/local/bin/sing-box check -c /etc/sing-box/config.json.dashboard-candidate')
 with s.open('/etc/nginx/conf.d/joysol-proxy.conf') as f:ng=f.read().decode()
 start='# JOYSOL MONITOR BEGIN';end='# JOYSOL MONITOR END'
 if start in ng:ng=ng[:ng.index(start)]+ng[ng.index(end)+len(end):]
 block='''
    # JOYSOL MONITOR BEGIN
    location = /monitor { return 302 /monitor/; }
    location ^~ /monitor/ {
        proxy_pass http://127.0.0.1:8307;
        proxy_set_header Host $host;
        proxy_set_header X-Dashboard-Client-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_hide_header Cache-Control;
        add_header Cache-Control "private, no-store" always;
        access_log off;
    }
    location = /_joysol_ws_gate {
        internal;
        proxy_pass http://127.0.0.1:8307/internal/gate;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Origin-Guard $http_x_joysol_origin;
        proxy_set_header X-ESA-Client-IP "";
        proxy_set_header X-Origin-XFF $http_x_forwarded_for;
    }
    # JOYSOL MONITOR END
'''
 # Target HTTPS8443 server only.
 pos=ng.index('listen 8443');pos=ng.index('\n',pos)+1
 ng=ng[:pos]+block+ng[pos:]
 if '--gate' in sys.argv and 'auth_request /_joysol_ws_gate;' not in ng:
  needle='proxy_pass http://127.0.0.1:10000;'
  if needle not in ng:raise RuntimeError('WS upstream not found; refusing mutation')
  ng=ng.replace(needle,'''auth_request /_joysol_ws_gate;
        auth_request_set $joysol_verified_client $upstream_http_x_verified_client_ip;
        proxy_set_header X-Forwarded-For $joysol_verified_client;
        '''+needle)
 with s.open('/etc/nginx/conf.d/joysol-proxy.conf','w') as f:f.write(ng)
 s.close()
 try:run('nginx -t')
 except Exception:
  run('cp -a /etc/nginx/conf.d/joysol-proxy.conf.dashboard-'+timestamp+'.bak /etc/nginx/conf.d/joysol-proxy.conf')
  raise
 run('set -e; if ! cmp -s /etc/sing-box/config.json.dashboard-candidate /etc/sing-box/config.json; then cp /etc/sing-box/config.json.dashboard-candidate /etc/sing-box/config.json; systemctl restart sing-box.service; fi; systemctl daemon-reload; systemctl enable joysol-dashboard.service; systemctl restart joysol-dashboard.service; systemctl reload nginx')
finally:client.close()
