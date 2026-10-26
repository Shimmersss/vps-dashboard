#!/bin/sh
set -eu
# Fresh-server deployment only; refuses to overwrite an existing installation.
test ! -e /usr/local/bin/sing-box
test ! -e /etc/sing-box/config.json
test ! -e /etc/systemd/system/sing-box.service
python3 - <<'PY'
import socket,ssl
with socket.socket() as s:
 s.bind(('0.0.0.0',443))
ctx=ssl.create_default_context()
with socket.create_connection(('www.microsoft.com',443),timeout=15) as s:
 with ctx.wrap_socket(s,server_hostname='www.microsoft.com') as tls:
  assert tls.version()=='TLSv1.3'
PY
python3 - <<'PY'
import hashlib,io,json,os,tarfile,urllib.request
version='1.14.3'
r=json.load(urllib.request.urlopen(f'https://api.github.com/repos/SagerNet/sing-box/releases/tags/v{version}',timeout=30))
asset=next(x for x in r['assets'] if x['name']==f'sing-box-{version}-linux-amd64.tar.gz')
expected=asset.get('digest','')
assert expected.startswith('sha256:'),'Release asset missing SHA256 digest'
data=urllib.request.urlopen(asset['browser_download_url'],timeout=90).read()
actual='sha256:'+hashlib.sha256(data).hexdigest()
assert actual==expected,'Release SHA256 mismatch'
with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tar:
 binary=tar.extractfile(f'sing-box-{version}-linux-amd64/sing-box').read()
fd=os.open('/usr/local/bin/sing-box',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o755)
with os.fdopen(fd,'wb') as f:f.write(binary)
os.makedirs('/etc/sing-box',mode=0o750,exist_ok=True)
with open('/etc/sing-box/release.json','w') as f:
 json.dump({'version':version,'archive_sha256':actual,'url':asset['browser_download_url']},f,indent=2)
print('Official release installed; SHA256 verified:',version)
PY
id sing-box >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin sing-box
python3 - <<'PY'
import datetime,json,os,pwd,secrets,subprocess,uuid
backup='/root/joysol-proxy-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
os.mkdir(backup,0o700)
with open(backup+'/manifest.json','w') as f:
 json.dump({'preexisting_service':False,'preexisting_config':False,'note':'Fresh deployment; rollback by stopping and disabling sing-box. No previous proxy configuration.'},f,indent=2)
keys={line.split(':',1)[0].strip():line.split(':',1)[1].strip() for line in subprocess.check_output(['/usr/local/bin/sing-box','generate','reality-keypair'],text=True).splitlines() if ':' in line}
uid=str(uuid.uuid4());sid=secrets.token_hex(8)
config={'log':{'level':'warn','timestamp':True},'inbounds':[{'type':'vless','tag':'vless-reality-in','listen':'0.0.0.0','listen_port':443,'users':[{'name':'joysol','uuid':uid,'flow':'xtls-rprx-vision'}],'tls':{'enabled':True,'server_name':'www.microsoft.com','reality':{'enabled':True,'handshake':{'server':'www.microsoft.com','server_port':443},'private_key':keys['PrivateKey'],'short_id':[sid]}}}],'outbounds':[{'type':'direct','tag':'direct'}],'route':{'rules':[{'ip_is_private':True,'action':'reject'}],'final':'direct'}}
owner=pwd.getpwnam('sing-box')
def save(path,obj):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(obj,f,indent=2)
os.chown('/etc/sing-box',0,owner.pw_gid)
os.chmod('/etc/sing-box',0o750)
save('/etc/sing-box/config.json',config)
os.chown('/etc/sing-box/config.json',owner.pw_uid,owner.pw_gid)
save('/root/joysol-proxy-client.json',{'uuid':uid,'public_key':keys['PublicKey'],'short_id':sid,'server_name':'www.microsoft.com','port':443})
print('BACKUP_DIR='+backup)
PY
/usr/local/bin/sing-box check -c /etc/sing-box/config.json
cat > /etc/systemd/system/sing-box.service <<'EOF'
[Unit]
Description=Joysol sing-box proxy (VLESS Reality)
Wants=network-online.target
After=network-online.target

[Service]
User=sing-box
Group=sing-box
ExecStartPre=/usr/local/bin/sing-box check -c /etc/sing-box/config.json
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=65536
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sing-box
sleep 2
systemctl is-active sing-box
systemctl is-enabled sing-box
ss -lnt 'sport = :443'
/usr/local/bin/sing-box version | head -1
