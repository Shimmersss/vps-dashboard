"""Internal helper; run with .venv/bin/python -B scripts/remote.py < script.sh."""
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('runner',ROOT/'singbox-exit-ops/scripts/ssh_run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def connect():
 s=m.load_settings(ROOT/'.env')
 c=m.paramiko.SSHClient();c.load_host_keys(str(ROOT/'.ssh/known_hosts'))
 c.connect(s['SSH_HOST'],port=s['SSH_PORT'],username=s['SSH_USER'],password=s['SSH_RUN_PASSWORD'],look_for_keys=False,allow_agent=False,timeout=20)
 # The local path may go through a proxy that drops idle TCP after ~30 s; keep the session alive.
 c.get_transport().set_keepalive(10)
 return c,s
if __name__=='__main__':
 with connect()[0] as c:
  result=m.execute(c,sys.stdin.read(),300)
  print(result['stdout'],end='');print(result['stderr'],end='',file=sys.stderr);sys.exit(result['rc'])
