"""Persist UU Remote DIRECT in exports and this Mac's current Clash profile."""
import json,os,shutil,subprocess,time,zipfile
from pathlib import Path
import yaml
from client_rules import clash_rules,sing_rules
ROOT=Path(__file__).resolve().parents[1];stamp=time.strftime('%Y%m%d%H%M%S');backup=ROOT/'verification'/('uu-direct-'+stamp);backup.mkdir(mode=0o700)
root=Path.home()/'Library/Application Support/io.github.clash-verge-rev.clash-verge-rev'
def preserve(path,label):
 shutil.copy2(path,backup/label);(backup/label).chmod(0o600)
def put(path,text):
 with path.open('w') as f:f.write(text)
 path.chmod(0o600)
def prepend(config):
 config['rules']=clash_rules()+[r for r in config.get('rules',[]) if r not in clash_rules()]
 config['find-process-mode']='strict'
 return config
for folder in [ROOT/'exports',ROOT/'exports/esa']:
 for name in ['clash-verge.yaml','sing-box-client.json']:
  p=folder/name;preserve(p,folder.name+'-'+name)
  if name.endswith('.yaml'):put(p,yaml.safe_dump(prepend(yaml.safe_load(p.read_text())),allow_unicode=True,sort_keys=False))
  else:
   config=json.loads(p.read_text());rules=config['route']['rules'];config['route']['rules']=sing_rules()+[r for r in rules if r not in sing_rules()];put(p,json.dumps(config,indent=2)+'\n')
 archive=folder/('proxy-esa-configs.zip' if folder.name=='esa' else 'proxy-configs.zip')
 preserve(archive,folder.name+'-'+archive.name)
 with zipfile.ZipFile(archive) as z:entries={n:z.read(n) for n in z.namelist()}
 for name in ['clash-verge.yaml','sing-box-client.json']:entries[name]=(folder/name).read_bytes()
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for name,data in entries.items():z.writestr(name,data)
 archive.chmod(0o600)
profiles=yaml.safe_load((root/'profiles.yaml').read_text());current=next(x for x in profiles['items'] if x.get('uid')==profiles['current'])
for p in [root/'profiles'/current['file'],root/'clash-verge.yaml']:
 preserve(p,p.name);put(p,yaml.safe_dump(prepend(yaml.safe_load(p.read_text())),allow_unicode=True,sort_keys=False))
# Keep the existing game relay exception and ensure future profile regeneration
# also prepends UU Remote DIRECT, even if a subscription is temporarily stale.
p=root/'profiles/Script.js';preserve(p,'Script.js');script=p.read_text()
if '// JOYSOL UU DIRECT' not in script:
 pos=script.rfind('return config;');assert pos>=0,'Unexpected global enhancement script'
 addition='// JOYSOL UU DIRECT\n  const uuDirect = '+json.dumps(clash_rules())+';\n  config.rules = [...uuDirect, ...(config.rules || []).filter(rule => !uuDirect.includes(rule))];\n  config["find-process-mode"] = "strict";\n  '
 put(p,script[:pos]+addition+script[pos:])
config=root/'clash-verge.yaml'
check=subprocess.run(['/opt/homebrew/bin/mihomo','-t','-f',str(config),'-d',str(root)],capture_output=True,text=True,timeout=40)
assert check.returncode==0,'Native Mihomo check failed; inspect private configuration backup'
payload=json.dumps({'path':str(config)})
r=subprocess.run(['curl','--silent','--show-error','--unix-socket','/tmp/verge/verge-mihomo.sock','-X','PUT','http://localhost/configs?force=true','-H','Content-Type: application/json','--data-binary',payload,'-w','\n%{http_code}'],capture_output=True,text=True,timeout=30)
assert r.returncode==0 and r.stdout.strip().endswith('204'),'Controller reload failed'
print('Exports, active profile, persistent enhancement and running core updated; credentials omitted.')
