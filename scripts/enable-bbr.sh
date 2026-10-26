#!/bin/sh
set -eu
# Uses the existing kernel module; no kernel upgrade or proxy restart.
modinfo tcp_bbr >/dev/null
python3 - <<'PY'
import datetime,json,os,shutil,subprocess
backup='/root/joysol-network-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
os.mkdir(backup,0o700)
paths=['/etc/sysctl.d/90-joysol-bbr.conf','/etc/modules-load.d/joysol-bbr.conf']
manifest={'tcp_congestion_control':subprocess.check_output(['sysctl','-n','net.ipv4.tcp_congestion_control'],text=True).strip(),'files':{}}
for i,p in enumerate(paths):
 manifest['files'][p]=os.path.exists(p)
 if os.path.exists(p):shutil.copy2(p,backup+'/'+str(i)+'.conf')
with open(backup+'/manifest.json','w') as f:json.dump(manifest,f,indent=2)
subprocess.run(['modprobe','tcp_bbr'],check=True)
available=subprocess.check_output(['sysctl','-n','net.ipv4.tcp_available_congestion_control'],text=True).split()
assert 'bbr' in available
subprocess.run(['sysctl','-w','net.ipv4.tcp_congestion_control=bbr'],check=True)
with open(paths[0],'w') as f:f.write('# Joysol: BBR for new TCP connections; existing qdiscs retained.\nnet.ipv4.tcp_congestion_control = bbr\n')
with open(paths[1],'w') as f:f.write('tcp_bbr\n')
print('BACKUP_DIR='+backup)
PY
sysctl -p /etc/sysctl.d/90-joysol-bbr.conf
sysctl net.ipv4.tcp_congestion_control net.ipv4.tcp_available_congestion_control
systemctl is-active sing-box
