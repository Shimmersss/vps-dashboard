"""Read proxy URL from stdin; preflight or apply AI routing via verified SSH."""
import argparse,json,sys
from urllib.parse import urlsplit,unquote
from remote import connect
parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
u=urlsplit(sys.stdin.read().strip())
assert u.scheme=='http' and u.hostname and u.port and u.username and u.password,'Expected authenticated HTTP proxy'
settings={'host':u.hostname,'port':u.port,'username':unquote(u.username),'password':unquote(u.password),'apply':args.apply}
remote=r'''
import datetime,json,os,pwd,shutil,subprocess,sys
settings=SETTINGS
path='/etc/sing-box/config.json'
config=json.load(open(path))
print('service:',subprocess.check_output(['systemctl','is-active','sing-box'],text=True).strip(),flush=True)
print('inbounds:',json.dumps([{'type':x['type'],'port':x.get('listen_port')} for x in config['inbounds']]),flush=True)
print('outbound tags:',[x['tag'] for x in config['outbounds']],flush=True)
def fetch(url,proxy=True):
 opts=''
 if proxy:
  opts='proxy = '+json.dumps('http://'+settings['host']+':'+str(settings['port']))+'\nproxy-user = '+json.dumps(settings['username']+':'+settings['password'])+'\n'
 p=subprocess.run(['curl','--config','-','--silent','--show-error','--max-time','35','--dump-header','/dev/stderr','--write-out','\n%{http_code}',url],input=opts,capture_output=True,text=True,timeout=40)
 body,_,status=p.stdout.rpartition('\n')
 return {'rc':p.returncode,'status':status,'body':body[:1600],'challenge':'cf-mitigated: challenge' in p.stderr.lower()}
if not settings['apply']:
 results={}
 for name,url in [('exit-ip','https://api.ipify.org'),('chatgpt-trace','https://chatgpt.com/cdn-cgi/trace'),('claude-trace','https://claude.ai/cdn-cgi/trace'),('google','https://www.google.com/generate_204'),('gemini','https://gemini.google.com/'),('openai-region','https://api.openai.com/v1/models')]:
  result=fetch(url);results[name]=result
  if name=='gemini':result['body']='[HTML omitted]'
  print(json.dumps({'test':name,**result}),flush=True)
 assert results['exit-ip']['body'].strip()=='198.51.100.44','Unexpected residential exit IP'
 for name in results:assert results[name]['rc']==0,'Proxy connectivity failed: '+name
 assert results['google']['status']=='204'
 assert results['gemini']['status'] in ('200','301','302','303','307','308')
 assert 'unsupported_country_region_territory' not in results['openai-region']['body']
 assert results['openai-region']['status']=='401'
 for name in ('chatgpt-trace','claude-trace'):
  assert 'ip=198.51.100.44' in results[name]['body'],'Trace verification failed: '+name
 print('PREFLIGHT_PASSED',flush=True)
 sys.exit(0)
backup='/root/joysol-ai-exit-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
os.mkdir(backup,0o700);shutil.copy2(path,backup+'/config.json');os.chmod(backup+'/config.json',0o600)
domains=['openai.com','chatgpt.com','oaistatic.com','oaiusercontent.com','chat.com','auth0.openai.com','anthropic.com','claude.ai','claude.com','claudeusercontent.com','google.com','googleapis.com','gstatic.com','googleusercontent.com','google','gvt1.com','gvt2.com','recaptcha.net','deepmind.com']
regex=[r'(^|\.)google\.[a-z]{2,3}(\.[a-z]{2})?$']
tag='decodo-us-10003'
assert tag not in [x['tag'] for x in config['outbounds']],'Target outbound already exists; inspect before reapplying'
config['outbounds'].append({'type':'http','tag':tag,'server':settings['host'],'server_port':settings['port'],'username':settings['username'],'password':settings['password']})
base=config['route'].get('rules',[])
config['route']['rules']=base+[{'action':'sniff'},{'domain_suffix':domains,'network':'udp','action':'reject'},{'domain_regex':regex,'network':'udp','action':'reject'},{'domain_suffix':domains,'network':'tcp','action':'route','outbound':tag},{'domain_regex':regex,'network':'tcp','action':'route','outbound':tag}]
# The old final direct outbound remains unchanged.
owner=pwd.getpwnam('sing-box')
fd=os.open(path,os.O_WRONLY|os.O_TRUNC)
with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);json.dump(config,f,indent=2)
os.chown(path,owner.pw_uid,owner.pw_gid)
check=subprocess.run(['/usr/local/bin/sing-box','check','-c',path],capture_output=True,text=True)
if check.returncode:
 shutil.copy2(backup+'/config.json',path);os.chown(path,owner.pw_uid,owner.pw_gid)
 print('CHECK_FAILED_CONFIG_RESTORED',flush=True);sys.exit(1)
print('BACKUP_DIR='+backup,flush=True)
print('CHECK_OK',flush=True)
r=subprocess.run(['systemctl','restart','sing-box'])
if r.returncode:
 shutil.copy2(backup+'/config.json',path);os.chown(path,owner.pw_uid,owner.pw_gid)
 subprocess.run(['systemctl','restart','sing-box'])
 print('RESTART_FAILED_ROLLED_BACK',flush=True);sys.exit(1)
print('service:',subprocess.check_output(['systemctl','is-active','sing-box'],text=True).strip(),flush=True)
print('final:',config['route']['final'],flush=True)
print('AI domains:',json.dumps(domains),flush=True)
'''.replace('SETTINGS',repr(settings),1)
with connect()[0] as c:
 stdin,stdout,stderr=c.exec_command('python3 -u -',timeout=300)
 stdin.write(remote);stdin.flush();stdin.channel.shutdown_write()
 # Remote prints only sanitized summaries. Never echo the script or credentials.
 for line in stdout:print(line,end='',flush=True)
 err=stderr.read().decode()
 rc=stdout.channel.recv_exit_status()
 if err:print('Remote diagnostic (redacted on failure)' if rc else err,file=sys.stderr)
 sys.exit(rc)
