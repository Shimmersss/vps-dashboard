"""Apply CN direct / overseas proxy while retaining explicit app exceptions."""
import json,os,shutil,subprocess,time,zipfile
from pathlib import Path
import yaml
from client_rules import china_split,CN_CLASH_RULES
ROOT=Path(__file__).resolve().parents[1];stamp=time.strftime('%Y%m%d%H%M%S');backup=ROOT/'verification'/('china-split-'+stamp);backup.mkdir(mode=0o700)
root=Path.home()/'Library/Application Support/io.github.clash-verge-rev.clash-verge-rev'
def save(p,text,label):
 shutil.copy2(p,backup/label);(backup/label).chmod(0o600)
 p.write_text(text);p.chmod(0o600)
for folder in [ROOT/'exports',ROOT/'exports/esa']:
 for name in ['clash-verge.yaml','sing-box-client.json']:
  p=folder/name
  config=yaml.safe_load(p.read_text()) if name.endswith('.yaml') else json.loads(p.read_text())
  china_split(config,'clash' if name.endswith('.yaml') else 'sing')
  text=yaml.safe_dump(config,allow_unicode=True,sort_keys=False) if name.endswith('.yaml') else json.dumps(config,indent=2)+'\n'
  save(p,text,folder.name+'-'+name)
 archive=folder/('proxy-esa-configs.zip' if folder.name=='esa' else 'proxy-configs.zip');shutil.copy2(archive,backup/(folder.name+'-'+archive.name))
 with zipfile.ZipFile(archive) as z:entries={n:z.read(n) for n in z.namelist()}
 for name in ['clash-verge.yaml','sing-box-client.json']:entries[name]=(folder/name).read_bytes()
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for name,data in entries.items():z.writestr(name,data)
 archive.chmod(0o600)
p=yaml.safe_load((root/'profiles.yaml').read_text());current=next(i for i in p['items'] if i.get('uid')==p['current'])
for path in [root/'profiles'/current['file'],root/'clash-verge.yaml']:
 config=china_split(yaml.safe_load(path.read_text()),'clash');save(path,yaml.safe_dump(config,allow_unicode=True,sort_keys=False),path.name)
p=root/'profiles/Script.js';script=p.read_text();start='  // JOYSOL CHINA SPLIT\n'
if start in script:script=script[:script.index(start)]+script[script.index('  return config;',script.index(start)):]
pos=script.rfind('  return config;');assert pos>=0
block=start+'  const cnSplit = '+json.dumps(CN_CLASH_RULES)+';\n  config.mode = "rule";\n  config.rules = [...(config.rules || []).filter(rule => !cnSplit.includes(rule) && !/^(MATCH|FINAL),/.test(rule)), ...cnSplit, "MATCH,PROXY"];\n'
save(p,script[:pos]+block+script[pos:],'Script.js')
check=subprocess.run(['/opt/homebrew/bin/mihomo','-t','-f',str(root/'clash-verge.yaml'),'-d',str(root)],capture_output=True,text=True,timeout=90)
if check.returncode:raise RuntimeError('Mihomo rejected candidate: '+check.stdout[-1000:]+check.stderr[-1000:])
# Hot reload retains existing tunnels; new connections use the new policy.
r=subprocess.run(['curl','-sS','--unix-socket','/tmp/verge/verge-mihomo.sock','-X','PUT','http://localhost/configs?force=true','-H','Content-Type: application/json','--data-binary',json.dumps({'path':str(root/'clash-verge.yaml')}),'-w','\n%{http_code}'],capture_output=True,text=True,timeout=60)
assert r.returncode==0 and r.stdout.strip().endswith('204'),'Controller reload failed'
print('CN direct / overseas proxy applied to exports, current profile, enhancement and live core.')
