"""Validate exported credentials from this Mac without changing system proxy."""
import json,os,socket,ssl,struct,subprocess,time
from pathlib import Path
from remote import ROOT,connect,m
out=ROOT/'verification/esa'
out.mkdir(mode=0o700,exist_ok=True);out.chmod(0o700)
def save(name,value):
 fd=os.open(out/name,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);f.write(value)
config=(ROOT/'exports/esa/clash-verge.yaml').read_text().replace('mixed-port: 7890','mixed-port: 17890')
save('mihomo-test.yaml',config)
# Check native sing-box client config using the installed server binary, through stdin.
with connect()[0] as c:
 content=(ROOT/'exports/esa/sing-box-client.json').read_text()
 # Remote check consumes stdin, so no remote temporary credential file is created.
 stdin,stdout,stderr=c.exec_command('/usr/local/bin/sing-box check -c /dev/stdin',timeout=30)
 stdin.write(content);stdin.flush();stdin.channel.shutdown_write()
 native_out=stdout.read().decode();native_err=stderr.read().decode();rc=stdout.channel.recv_exit_status()
 assert rc==0, 'Native client configuration check failed'
 save('sing-box-client-check.txt',f'rc={rc}\n'+native_out+native_err)
 print('Native sing-box client configuration: OK',flush=True)
logfd=os.open(out/'mihomo-test.log',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
results=[]
with os.fdopen(logfd,'w') as log:
 proc=subprocess.Popen(['/opt/homebrew/bin/mihomo','-d',str(out),'-f',str(out/'mihomo-test.yaml')],stdout=log,stderr=subprocess.STDOUT)
 try:
  for _ in range(100):
   if proc.poll() is not None:raise RuntimeError('Mihomo exited')
   try:
    with socket.create_connection(('127.0.0.1',17890),timeout=.1):break
   except OSError:time.sleep(.1)
  else:raise TimeoutError('Local proxy did not start')
  tests=[('exit-ip','https://api.ipify.org'),('cloudflare-trace','https://www.cloudflare.com/cdn-cgi/trace'),('google','https://www.google.com/generate_204'),('openai-region','https://api.openai.com/v1/models'),('chatgpt-trace','https://chatgpt.com/cdn-cgi/trace'),('claude-trace','https://claude.ai/cdn-cgi/trace'),('gemini','https://gemini.google.com/')]
  for name,url in tests:
   r=subprocess.run(['curl','--noproxy','','--proxy','socks5h://127.0.0.1:17890','--max-time','35','--silent','--show-error','--write-out','\n%{http_code}',url],capture_output=True,text=True,timeout=40)
   body,_,code=r.stdout.rpartition('\n')
   record={'name':name,'url':url,'curl_rc':r.returncode,'http_status':code,'body':('[HTML omitted]' if name=='gemini' else body[:2500]),'error':r.stderr[:1000]}
   results.append(record);print(f'{name}: curl_rc={r.returncode}, HTTP={code}',flush=True)
  save('end-to-end.json',json.dumps(results,indent=2)+'\n')
  # Hold a TLS session for google.com and observe the service's upstream socket.
  def receive(sock,n):
   data=b''
   while len(data)<n:
    chunk=sock.recv(n-len(data))
    if not chunk:raise ConnectionError('SOCKS connection closed')
    data+=chunk
   return data
  with socket.create_connection(('127.0.0.1',17890),timeout=20) as raw:
   raw.sendall(b'\x05\x01\x00');assert receive(raw,2)==b'\x05\x00'
   host=b'www.google.com'
   raw.sendall(b'\x05\x01\x00\x03'+bytes([len(host)])+host+struct.pack('!H',443))
   header=receive(raw,4);assert header[1]==0
   if header[3]==1:receive(raw,4)
   elif header[3]==4:receive(raw,16)
   elif header[3]==3:receive(raw,receive(raw,1)[0])
   receive(raw,2)
   with ssl.create_default_context().wrap_socket(raw,server_hostname='www.google.com') as tls:
    with connect()[0] as ssh:
     r=m.execute(ssh,"ss -Hntp state established | grep sing-box || true",20)
    proof={'target':'www.google.com:443','tls':tls.version(),'server_sockets':r['stdout']}
    save('google-upstream-proof.json',json.dumps(proof,indent=2)+'\n')
    assert ':10003' in r['stdout'],'Expected Decodo upstream for held Google TLS session'
    print('Google live TLS session uses Decodo upstream port 10003: OK',flush=True)
 finally:
  proc.terminate()
  try:proc.wait(timeout=10)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
expected=m.load_settings(ROOT/'.env')['SSH_HOST']
assert results[0]['curl_rc']==0 and results[0]['body'].strip()==expected,'Exit IP did not match VPS'
assert results[1]['curl_rc']==0 and 'ip='+expected in results[1]['body'],'Cloudflare trace mismatch'
assert results[2]['curl_rc']==0 and results[2]['http_status']=='204','Google HTTPS failed'
for name in ('chatgpt-trace','claude-trace'):
 result=next(r for r in results if r['name']==name)
 assert result['curl_rc']==0 and 'ip=198.51.100.44' in result['body'],name+' residential exit mismatch'
assert next(r for r in results if r['name']=='gemini')['http_status']=='200'
openai=next(r for r in results if r['name']=='openai-region')
assert openai['http_status']=='401' and 'unsupported_country_region_territory' not in openai['body']
print('Public ESA WebSocket/TLS AI routing checks: PASSED',flush=True)
