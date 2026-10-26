"""Download both published formats and test each independent ESA upstream."""
import base64,json,os,socket,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
links=json.loads((ROOT/'exports/oxylabs/subscription-links.json').read_text())
original=json.loads((ROOT/'exports/subscription-links.json').read_text())
links['original']={'clash':original['clash'],'expected_ip':'198.51.100.44'}
parent=ROOT/'verification/oxylabs';parent.mkdir(mode=0o700,exist_ok=True)
assets=Path.home()/'Library/Application Support/io.github.clash-verge-rev.clash-verge-rev'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));results={}
for key in ['8001','8002','original']:
 item=links[key];d=parent/key;d.mkdir(mode=0o700,exist_ok=True)
 for asset in ['geosite.dat','geoip.dat','Country.mmdb']:
  dest=d/asset
  if not dest.exists():dest.symlink_to(assets/asset)
 clash=opener.open(item['clash'],timeout=30).read()
 if key!='original':
  nodes=opener.open(item['shadowrocket'],timeout=30).read()
  assert nodes==(ROOT/('exports/oxylabs/oxy-'+key+'-nodes.txt')).read_bytes()
 expected_file=ROOT/('exports/oxylabs/oxy-'+key+'-clash.yaml') if key!='original' else ROOT/'exports/esa/clash-verge.yaml'
 assert clash==expected_file.read_bytes()
 cfg=json.loads(clash) if key!='original' else json.loads(subprocess.run(['python3','-c','import yaml,json,sys;print(json.dumps(yaml.safe_load(sys.stdin)))'],input=clash,capture_output=True,check=True).stdout)
 node=cfg['proxies'][0]
 if key!='original':
  uri=base64.b64decode(nodes).decode();assert node['uuid'] in uri and '@proxy.example.com:443' in uri
 with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
 cfg['mixed-port']=port
 path=d/'client.json';path.write_text(json.dumps(cfg));path.chmod(0o600)
 check=subprocess.run(['/opt/homebrew/bin/mihomo','-t','-d',str(d),'-f',str(path)],capture_output=True,timeout=40);assert check.returncode==0,'Mihomo candidate rejected'
 logpath=d/'mihomo.log';logpath.touch(mode=0o600)
 with logpath.open('w') as logfile:
  p=subprocess.Popen(['/opt/homebrew/bin/mihomo','-d',str(d),'-f',str(path)],stdout=logfile,stderr=subprocess.STDOUT)
  try:
   for attempt in range(100):
    if p.poll() is not None:raise RuntimeError('Mihomo exited')
    try:
     with socket.create_connection(('127.0.0.1',port),timeout=.1):break
    except OSError:time.sleep(.1)
   tests={}
   for name,url in [('ordinary','https://api.ipify.org'),('chatgpt','https://chatgpt.com/cdn-cgi/trace'),('claude','https://claude.ai/cdn-cgi/trace'),('google','https://www.google.com/generate_204'),('openai','https://api.openai.com/v1/models')]:
    r=subprocess.run(['curl','--noproxy','','--proxy','http://127.0.0.1:'+str(port),'--silent','--show-error','--max-time','35','--write-out','\n%{http_code}',url],capture_output=True,text=True,timeout=40)
    body,_,code=r.stdout.rpartition('\n');assert r.returncode==0,name+' network request failed'
    if name=='ordinary':assert body.strip()=='203.0.113.10','Ordinary traffic check failed: HTTP '+code+'; body='+body[:100]
    elif name in ('chatgpt','claude'):assert 'ip='+item['expected_ip'] in body,name+' used unexpected upstream'
    elif name=='google':assert code=='204'
    elif name=='openai':assert code=='401' and 'unsupported_country_region_territory' not in body
    tests[name]={'status':int(code),'expected_exit_verified':True};print(key,name,code,'PASS',flush=True)
   results[key]={'published_formats_verified':True,'esa':True,'expected_ai_ip':item['expected_ip'],'tests':tests,'domestic_rule_retained':all(r in cfg['rules'] for r in ['GEOSITE,cn,DIRECT','GEOIP,CN,DIRECT']),'uu_rule_retained':'PROCESS-NAME,UURemote,DIRECT' in cfg['rules']}
  finally:
   p.terminate()
   try:p.wait(timeout=8)
   except subprocess.TimeoutExpired:p.kill();p.wait()
out=parent/'end-to-end.json';out.write_text(json.dumps(results,indent=2)+'\n');out.chmod(0o600)
print('Both ESA variants and their four published links: PASSED')
