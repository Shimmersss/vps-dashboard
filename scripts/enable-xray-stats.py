"""Enable the read-only Xray StatsService the dashboard needs, for instances listed in dashboard/deploy/site.json.

Usage: .venv/bin/python -B scripts/enable-xray-stats.py [--instance ID] [--check-only]

Per instance (settings.xray.instances[].service / config_path / api_address, optional stats_overlay): reads the
unit's real ExecStart, builds an overlay with StatsService on the loopback API address and the original effective
`policy` plus statistics switches (Xray replaces whole top-level objects across -config files), runs
`xray run -test`, then appends `-config <overlay>` through a systemd drop-in. Without --check-only the Xray service
restarts (its existing connections drop for a few seconds); a failed self-check restores the previous drop-in and
overlay. Original config files are never modified; an existing api block or -confdir layout is refused.
"""
import argparse
import json
import shlex
from pathlib import Path

import siteprofile
from remote import connect, m

parser = argparse.ArgumentParser()
parser.add_argument('--instance', help='only this instance id')
parser.add_argument('--check-only', action='store_true')
args = parser.parse_args()
profile = siteprofile.load()
instances = (profile.get('settings', {}).get('xray') or {}).get('instances') or []
if args.instance:
    instances = [i for i in instances if i['id'] == args.instance]
if not instances:
    raise SystemExit('site.json 的 settings.xray.instances 中没有可处理的实例')
REMOTE = (Path(__file__).resolve().parent / 'xray_stats_remote.py').read_text()

failed = False
with connect()[0] as client:
    for instance in instances:
        params = {'id': instance['id'], 'service': instance['service'], 'config_path': instance['config_path'],
                  'api_address': instance['api_address'], 'apply': not args.check_only,
                  'overlay': instance.get('stats_overlay') or '/etc/joysol-xray/%s-stats.json' % instance['id'],
                  'backup_root': profile['remote']['backup_dir']}
        cmd = "python3 - %s <<'PY'\n%s\nPY" % (shlex.quote(json.dumps(params)), REMOTE)
        r = m.execute(client, cmd, 180)
        print('[%s] %s' % (instance['id'], r['stdout'].strip() or (r['stderr'] or '').strip()[-400:]))
        failed = failed or bool(r['rc'])
if failed:
    raise SystemExit('部分实例未完成，见上方输出')
