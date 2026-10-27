"""Runs ON the Xray server as root (sent by scripts/enable-xray-stats.py); prints one JSON summary, never config contents.

Adds the dashboard's read-only StatsService through an extra `-config` overlay appended to the unit's real
ExecStart (systemd drop-in). Xray replaces whole top-level objects across -config files, so the overlay carries
the effective `policy` of the original files plus the statistics switches; existing API blocks are never
replaced. The original config files are not modified.
"""
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
DROPIN = 'zz-joysol-dashboard-stats.conf'
API_TAG = 'joysol-dashboard-stats'


def scrub(text):
    return UUID.sub('<uuid>', text.strip())[-400:]


def exec_argv(show):
    """argv of the first command in `systemctl show -p ExecStart --value` output."""
    match = re.search(r'argv\[\]=(.*?) ;', show)
    if not match:
        raise SystemExit('无法读取服务 ExecStart')
    return shlex.split(match.group(1))


def config_files(argv, overlay):
    """(argv without our overlay, ordered -config files). Refuses layouts this tool cannot merge safely."""
    out, files, i = [], [], 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('-confdir', '--confdir') or arg.startswith(('-confdir=', '--confdir=')):
            raise SystemExit('服务使用 -confdir，请手工整合 StatsService（见 docs/xray.md）')
        if arg in ('-config', '--config', '-c'):
            if i + 1 >= len(argv):
                raise SystemExit('ExecStart 的 -config 缺少参数')
            path, step = argv[i + 1], 2
        elif arg.startswith(('-config=', '--config=', '-c=')):
            path, step = arg.split('=', 1)[1], 1
        else:
            out.append(arg)
            i += 1
            continue
        if path != overlay:
            files.append(path)
            out.extend(argv[i:i + step])
        i += step
    if not files:
        raise SystemExit('ExecStart 未显式使用 -config 文件')
    if any(not f.endswith('.json') for f in files):
        raise SystemExit('只支持 JSON 配置文件')
    return out, files


def last_defined(configs, key):
    """Xray multi-config rule: a later file's top-level object replaces the earlier one."""
    value = None
    for config in configs:
        if config.get(key) is not None:
            value = config[key]
    return value


def build_overlay(configs, api_address):
    """(overlay, plan). Raises SystemExit when an existing API block would have to be replaced."""
    api = last_defined(configs, 'api')
    plan = {'api': 'add'}
    overlay = {'stats': last_defined(configs, 'stats') or {}}
    if api:
        if 'StatsService' in (api.get('services') or []) and api.get('listen') == api_address:
            plan['api'] = 'existing'
        else:
            raise SystemExit('已有 api 配置（tag=%s, listen=%s），为免覆盖原有服务请手工整合 StatsService' % (
                api.get('tag', ''), api.get('listen', '经入站')))
    else:
        overlay['api'] = {'tag': API_TAG, 'listen': api_address, 'services': ['StatsService']}
    policy = copy.deepcopy(last_defined(configs, 'policy') or {})
    policy.setdefault('system', {}).update(statsInboundUplink=True, statsInboundDownlink=True,
                                           statsOutboundUplink=True, statsOutboundDownlink=True)
    levels = {0}
    for config in configs:
        for inbound in config.get('inbounds') or []:
            for client in (inbound.get('settings') or {}).get('clients') or []:
                if isinstance(client.get('level'), int):
                    levels.add(client['level'])
    for level in sorted(levels):
        policy.setdefault('levels', {}).setdefault(str(level), {}).update(
            statsUserUplink=True, statsUserDownlink=True, statsUserOnline=True)
    overlay['policy'] = policy
    plan.update(levels=sorted(levels), kept_policy_keys=sorted(k for k in policy if k != 'system'))
    return overlay, plan


def systemd_line(argv):
    return ' '.join(shlex.quote(a) for a in argv).replace('%', '%%')


def run(*cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise SystemExit('步骤失败 %s：%s' % (' '.join(cmd[:2]), scrub(r.stderr or r.stdout)))
    return r.stdout


def write(path, text, mode):
    tmp = path + '.joysol-new'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w') as f:
        os.fchmod(f.fileno(), mode)
        f.write(text)
    os.replace(tmp, path)


def main(p):
    service, overlay_path = p['service'], p['overlay']
    host, port = p['api_address'].rsplit(':', 1)
    if host not in ('127.0.0.1', '::1', '[::1]'):
        raise SystemExit('API 必须监听回环地址')
    argv = exec_argv(run('systemctl', 'show', '-p', 'ExecStart', '--value', service))
    binary = argv[0]
    base_argv, files = config_files(argv, overlay_path)
    if p['config_path'] not in files:
        raise SystemExit('站点配置的 config_path 不在该服务实际加载的 -config 列表中')
    configs = [json.load(open(f)) for f in files]
    overlay, plan = build_overlay(configs, p['api_address'])
    listening = subprocess.run(['ss', '-ltnH', 'sport = :' + port], capture_output=True, text=True).stdout
    if plan['api'] == 'add' and listening.strip() and overlay_path not in argv:
        raise SystemExit('回环端口 %s 已被占用' % port)
    stamp = time.strftime('%Y%m%d%H%M%S')
    backup = os.path.join(p['backup_root'], 'joysol-xray-stats-backup-%s-%s' % (p['id'], stamp))
    os.mkdir(backup, 0o700)
    dropin_dir = '/etc/systemd/system/%s.d' % service
    dropin = os.path.join(dropin_dir, DROPIN)
    for src in (dropin, overlay_path):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup, os.path.basename(src)))
    os.makedirs(os.path.dirname(overlay_path), mode=0o755, exist_ok=True)
    # No credentials in the overlay (api address, statistics switches, policy timeouts), so the
    # service user may read it whatever account it runs as.
    candidate = os.path.join(backup, 'overlay-candidate.json')
    write(candidate, json.dumps(overlay, indent=2) + '\n', 0o644)
    final_argv = base_argv + ['-config', overlay_path]
    test = subprocess.run([binary, 'run', '-test'] + [a for a in base_argv[1:] if a != 'run'] + ['-config', candidate],
                          capture_output=True, text=True, timeout=60)
    if test.returncode:
        raise SystemExit('xray -test 未通过：' + scrub(test.stderr or test.stdout))
    out = {'service': service, 'files': len(files), 'plan': plan, 'backup': backup, 'test': 'ok',
           'dropin': dropin, 'overlay': overlay_path, 'applied': False}
    if not p['apply']:
        print(json.dumps(out, ensure_ascii=False))
        return
    shutil.copy2(candidate, overlay_path)
    os.chmod(overlay_path, 0o644)
    os.makedirs(dropin_dir, mode=0o755, exist_ok=True)
    write(dropin, '# Joysol dashboard: read-only Xray StatsService overlay (scripts/enable-xray-stats.py)\n'
                  '[Service]\nExecStart=\nExecStart=%s\n' % systemd_line(final_argv), 0o644)
    run('systemctl', 'daemon-reload')
    subprocess.run(['systemctl', 'restart', service], timeout=60)
    ok, reason = False, ''
    for _ in range(20):
        time.sleep(1)
        active = subprocess.run(['systemctl', 'is-active', service], capture_output=True, text=True).stdout.strip()
        query = subprocess.run([binary, 'api', 'statsquery', '--server=' + p['api_address'], '-pattern', 'inbound>>>'],
                               capture_output=True, text=True, timeout=10)
        if active == 'active' and query.returncode == 0 and 'inbound>>>' in query.stdout:
            ok = True
            break
        reason = 'service %s; statsquery %s' % (active, scrub(query.stderr or query.stdout)[-160:])
    if not ok:
        for name, path in ((DROPIN, dropin), (os.path.basename(overlay_path), overlay_path)):
            saved = os.path.join(backup, name)
            if os.path.exists(saved):
                shutil.copy2(saved, path)
            elif os.path.exists(path):
                os.remove(path)
        subprocess.run(['systemctl', 'daemon-reload'])
        subprocess.run(['systemctl', 'restart', service], timeout=60)
        raise SystemExit('启用后自检失败，已恢复原 drop-in/叠加文件并重启：' + reason)
    out.update(applied=True, active=True, stats='ok')
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main(json.loads(sys.argv[1]))
