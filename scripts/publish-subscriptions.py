"""Publish token-protected HTTPS subscriptions without exposing server secrets."""
import base64,json,os,secrets
from remote import ROOT,connect
state=ROOT/'exports/subscription-links.json'
if state.exists():
 links=json.loads(state.read_text());token=links['token']
else:
 token=secrets.token_urlsafe(32)
base='https://proxy.example.com/subscription/'+token
links={'token':token,'clash':base+'/clash.yaml','v2ray':base+'/nodes.txt','sing_box':base+'/sing-box.json'}
files={'clash.yaml':(ROOT/'exports/esa/clash-verge.yaml').read_bytes(),'sing-box.json':(ROOT/'exports/esa/sing-box-client.json').read_bytes(),'nodes.txt':base64.b64encode((ROOT/'exports/esa/vless-esa.txt').read_bytes()+(ROOT/'exports/vless-reality.txt').read_bytes())+b'\n'}
remote=r"""
import datetime,grp,json,os,shutil,subprocess
state=STATE
path='/etc/nginx/conf.d/joysol-proxy.conf'
text=open(path).read()
already_published='# JOYSOL SUBSCRIPTIONS' in text
assert not already_published or '/subscription/'+state['token']+'/' in text,'Existing subscription uses a different token; inspect before replacing'
backup='/root/joysol-subscription-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
os.mkdir(backup,0o700);shutil.copy2(path,backup+'/joysol-proxy.conf');os.chmod(backup+'/joysol-proxy.conf',0o600)
folder='/var/lib/joysol-subscriptions'
os.makedirs(folder,mode=0o750,exist_ok=True);gid=grp.getgrnam('www-data').gr_gid;os.chown(folder,0,gid)
for name,data in state['files'].items():
 fd=os.open(folder+'/'+name,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o640)
 with os.fdopen(fd,'wb') as f:
  os.fchmod(f.fileno(),0o640);os.fchown(f.fileno(),0,gid);f.write(bytes(data))
locations='    # JOYSOL SUBSCRIPTIONS\n'
for name in state['files']:
 locations+='''    location = /subscription/'''+state['token']+'/'+name+''' {
        alias '''+folder+'/'+name+''';
        default_type application/octet-stream;
        add_header Cache-Control "private, no-store, no-cache, max-age=0" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header profile-update-interval "24" always;
        access_log off;
    }
'''
locations+='    location /subscription/ { return 404; }\n'
if not already_published:
 start=text.index('    listen 8443 ssl;');insert=text.index('    location / { return 404; }',start)
 text=text[:insert]+locations+text[insert:]
with open(path,'w') as f:f.write(text)
check=subprocess.run(['nginx','-t'],capture_output=True,text=True)
if check.returncode:
 shutil.copy2(backup+'/joysol-proxy.conf',path)
 raise RuntimeError('Nginx candidate rejected; restored backup')
subprocess.run(['systemctl','reload','nginx'],check=True)
print(json.dumps({'status':'published','backup':backup,'nginx':'configuration checked and reloaded'}))
"""
with connect()[0] as c:
 payload=remote.replace('STATE',repr({'token':token,'files':{k:list(v) for k,v in files.items()}}),1)
 stdin,stdout,stderr=c.exec_command('python3 -',timeout=60)
 stdin.write(payload);stdin.flush();stdin.channel.shutdown_write()
 output=stdout.read().decode();rc=stdout.channel.recv_exit_status()
 if rc:raise RuntimeError('Subscription publication failed; inspect remotely without printing secrets')
 print(output,end='')
fd=os.open(state,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);json.dump(links,f,indent=2)
print('Links saved privately in exports/subscription-links.json')
