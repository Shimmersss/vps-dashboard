#!/bin/sh
set -eu
# Larger TCP buffers for long-RTT client paths; no proxy restart, qdisc and BBR unchanged.
python3 - <<'PY'
import datetime,json,os,subprocess
backup='/root/joysol-network-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
os.mkdir(backup,0o700)
path='/etc/sysctl.d/91-joysol-tcp.conf'
values={
 'net.core.rmem_max':'16777216',
 'net.core.wmem_max':'16777216',
 'net.ipv4.tcp_rmem':'4096 131072 16777216',
 'net.ipv4.tcp_wmem':'4096 16384 16777216',
 'net.ipv4.tcp_notsent_lowat':'131072',
 'net.ipv4.tcp_slow_start_after_idle':'0',
 'net.ipv4.tcp_mtu_probing':'1',
}
before={k:' '.join(subprocess.check_output(['sysctl','-n',k],text=True).split()) for k in values}
manifest={'previous':before,'file':path,'file_existed':os.path.exists(path)}
if manifest['file_existed']:
 with open(path) as src,open(backup+'/91-joysol-tcp.conf','w') as dst:dst.write(src.read())
with open(backup+'/manifest.json','w') as f:json.dump(manifest,f,indent=2)
with open(path,'w') as f:
 f.write('# Joysol: TCP buffers for long-RTT proxy paths; see the backup manifest for rollback.\n')
 f.writelines('%s = %s\n'%(k,v) for k,v in values.items())
print('BACKUP_DIR='+backup)
PY
sysctl -p /etc/sysctl.d/91-joysol-tcp.conf
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc
systemctl is-active sing-box nginx joysol-dashboard
