"""Small repeatable before/after measurements; never changes system proxy.

Usage: benchmark-proxy.py <stage> [bulk_bytes]; stage is before/after or before-<x>/after-<x>. bulk_bytes adds a
larger download (3 runs) for throughput-bound changes such as TCP buffers.
"""
import json,os,socket,statistics,subprocess,sys,time
from pathlib import Path
from remote import ROOT
import re
stage=sys.argv[1];assert re.fullmatch(r'(before|after)(-[a-z0-9]+)?',stage)
bulk=int(sys.argv[2]) if len(sys.argv)>2 else 0
folder=ROOT/'verification'
fd=os.open(folder/f'network-{stage}-mihomo.log',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
results=[]
with os.fdopen(fd,'w') as log:
 p=subprocess.Popen(['/opt/homebrew/bin/mihomo','-d',str(folder),'-f',str(folder/'mihomo-test.yaml')],stdout=log,stderr=subprocess.STDOUT)
 try:
  for _ in range(100):
   if p.poll() is not None:raise RuntimeError('Test client exited')
   try:
    with socket.create_connection(('127.0.0.1',17890),timeout=.1):break
   except OSError:time.sleep(.1)
  else:raise TimeoutError('Test listener not ready')
  tests=[('vps-download','https://speed.cloudflare.com/__down?bytes=262144'),('ai-trace','https://chatgpt.com/cdn-cgi/trace')]
  if bulk:tests.append(('bulk-download','https://speed.cloudflare.com/__down?bytes=%d'%bulk))
  for name,url in tests:
   for i in range(3):
    r=subprocess.run(['curl','--noproxy','','-x','socks5h://127.0.0.1:17890','-sS','--max-time','120','-o','/dev/null','-w','%{json}',url],capture_output=True,text=True,timeout=130)
    d=json.loads(r.stdout)
    result={'test':name,'iteration':i+1,'curl_rc':r.returncode,**{k:d[k] for k in ['http_code','size_download','speed_download','time_connect','time_appconnect','time_starttransfer','time_total']}}
    results.append(result);print(json.dumps(result),flush=True)
 finally:
  p.terminate()
  try:p.wait(timeout=10)
  except subprocess.TimeoutExpired:p.kill();p.wait()
fd=os.open(folder/f'network-{stage}.json',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump(results,f,indent=2)
assert all(r['curl_rc']==0 and r['http_code']==200 for r in results)
for name in sorted({r['test'] for r in results}):
 rows=[r for r in results if r['test']==name]
 print(name,'median_total_seconds=',round(statistics.median(r['time_total'] for r in rows),3),'median_Bps=',round(statistics.median(r['speed_download'] for r in rows)))
