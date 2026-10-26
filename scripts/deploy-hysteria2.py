"""Add a Hysteria2 (QUIC/UDP) inbound next to Reality; never prints the node password.

Usage: .venv/bin/python -B scripts/deploy-hysteria2.py [--check-only] [--port 443]

--check-only: back up, copy the certificate for sing-box, install the renewal hook, write and `sing-box check`
a candidate config (also as the sing-box user), then stop. Without it the candidate replaces the live config
and sing-box restarts (existing Reality/ESA connections drop for a few seconds); a failed start restores the
backup. The domain comes from dashboard/deploy/site.json; the node password lives only in the remote
/root/joysol-hy2-client.json (0600) and in private exports.
"""
import argparse
import json

import siteprofile
from remote import connect, m

parser = argparse.ArgumentParser()
parser.add_argument('--check-only', action='store_true')
parser.add_argument('--port', type=int, default=443)
args = parser.parse_args()
profile = siteprofile.load()
params = {'domain': profile['host'], 'port': args.port, 'apply': not args.check_only,
          'config': profile['remote']['singbox_config'], 'binary': profile['remote']['singbox_binary'],
          'service': profile['remote']['singbox_service'], 'backup_root': profile['remote']['backup_dir']}

REMOTE = r'''
import json,os,secrets,shutil,subprocess,time
P=PARAMS
domain,cfg=P['domain'],P['config']
stamp=time.strftime('%Y%m%d%H%M%S')
backup=os.path.join(P['backup_root'],'joysol-hy2-backup-'+stamp)
os.mkdir(backup,0o700);shutil.copy2(cfg,backup+'/config.json')
hook='/etc/letsencrypt/renewal-hooks/deploy/joysol-sing-box.sh'
if os.path.exists(hook):shutil.copy2(hook,backup+'/joysol-sing-box.sh')
def run(*cmd,**kw):
    r=subprocess.run(cmd,capture_output=True,text=True,**kw)
    if r.returncode:raise SystemExit('step failed: '+' '.join(cmd[:3])+' :: '+(r.stderr or r.stdout).strip()[-300:])
    return r.stdout
live='/etc/letsencrypt/live/'+domain
assert os.path.exists(live+'/fullchain.pem'),'certificate for the site domain not found'
# Certificate copy readable by the sing-box user. Renewals rewrite these files in place (same inode),
# which sing-box watches and reloads without a restart.
tls='/etc/sing-box/tls'
run('install','-d','-o','sing-box','-g','sing-box','-m','700',tls)
crt,key=tls+'/'+domain+'.crt',tls+'/'+domain+'.key'
for src,dst in ((live+'/fullchain.pem',crt),(live+'/privkey.pem',key)):
    if not os.path.exists(dst):run('install','-o','sing-box','-g','sing-box','-m','600','/dev/null',dst)
    with open(src,'rb') as s,open(dst,'r+b') as d:
        d.truncate(0);d.write(s.read())
with open(hook,'w') as f:
    f.write('#!/bin/sh\n# Joysol: refresh the sing-box Hysteria2 certificate copy in place; sing-box reloads it.\n'
            'set -e\ncase " $RENEWED_DOMAINS " in *" %s "*) ;; *) exit 0;; esac\n'
            'cat %s/fullchain.pem > %s\ncat %s/privkey.pem > %s\n' % (domain,live,crt,live,key))
os.chmod(hook,0o755)
meta_path='/root/joysol-hy2-client.json'
if os.path.exists(meta_path):
    meta=json.load(open(meta_path))
else:
    meta={'name':'Joysol-Hy2','password':secrets.token_urlsafe(24)}
meta.update(port=P['port'],server_name=domain,alpn=['h3'])
fd=os.open(meta_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump(meta,f)
config=json.load(open(cfg))
inbound={'type':'hysteria2','tag':'hy2-in','listen':'0.0.0.0','listen_port':P['port'],
         'users':[{'name':meta['name'],'password':meta['password']}],
         # No server bandwidth: clients that declare up/down get Brutal, others fall back to BBR.
         'tls':{'enabled':True,'server_name':domain,'alpn':['h3'],'certificate_path':crt,'key_path':key}}
rest=[i for i in config['inbounds'] if i.get('tag')!='hy2-in']
clash=[i for i in rest if i.get('listen_port')==P['port'] and i.get('type') in ('hysteria','hysteria2','tuic')]
assert not clash,'another UDP inbound already uses this port'
at=next((n+1 for n,i in enumerate(rest) if i.get('tag')=='vless-reality-in'),len(rest))
config['inbounds']=rest[:at]+[inbound]+rest[at:]
cand=cfg+'.hy2-candidate'
fd=os.open(cand,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write(json.dumps(config,indent=2)+'\n')
shutil.chown(cand,'sing-box','sing-box')
run(P['binary'],'check','-c',cand)
run('runuser','-u','sing-box','--',P['binary'],'check','-c',cand)
out={'backup':backup,'candidate':'checked','inbounds':[i.get('tag') for i in config['inbounds']]}
if P['apply']:
    shutil.copy2(cand,cfg);shutil.chown(cfg,'sing-box','sing-box')
    subprocess.run(['systemctl','restart',P['service']])
    time.sleep(3)
    active=subprocess.run(['systemctl','is-active',P['service']],capture_output=True,text=True).stdout.strip()
    udp=subprocess.run(['ss','-ulnpH','sport = :%d'%P['port']],capture_output=True,text=True).stdout
    if active!='active' or 'sing-box' not in udp:
        shutil.copy2(backup+'/config.json',cfg);subprocess.run(['systemctl','restart',P['service']])
        raise SystemExit('sing-box did not come up with Hysteria2; restored backup and restarted')
    out.update(applied=True,service=active,udp_listener=True)
else:
    out['applied']=False
print(json.dumps(out))
'''

with connect()[0] as client:
    r = m.execute(client, "python3 - <<'PY'\n" + REMOTE.replace('PARAMS', repr(params), 1) + "\nPY", 120)
print(r['stdout'].strip())
if r['rc']:
    raise SystemExit((r['stderr'] or '').strip()[-400:] or 'remote step failed')
