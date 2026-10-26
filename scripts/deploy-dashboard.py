"""Deploy the node panel (v2) from dashboard/ using dashboard/deploy/site.json; prints no secrets.

    .venv/bin/python -B scripts/deploy-dashboard.py [--gate] [--check-only]

Mode comes from site.json settings: sing-box only (default), Xray only (singbox.enabled=false plus
xray.instances) or both (xray.instances with sing-box left enabled). Xray instances must already expose the
loopback StatsService (scripts/enable-xray-stats.py); this script never edits Xray.
Order: read-only preflight -> upload release (venv created / missing dependencies installed from the lock)
-> remote tests + in-memory migration rehearsal on the live ledger -> full backup -> write config/unit/nginx
-> switch + health check -> nginx reload. Works on a fresh host too (nothing to back up is skipped).
Any failure after the switch restores the backup automatically. sing-box restarts only if its API
service block actually changes (normally never). --gate enables the CDN WebSocket gate (sing-box only);
only use it once the CDN origin header is saved and verified (see docs/dashboard.md).
"""
import hashlib
import importlib.util
import json
import posixpath
import re
import secrets
import shlex
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote as r  # noqa: E402
import siteprofile as site_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'dashboard'
# The deploy examples are read by test_engine_modes on the server.
SHIP = ['app.py', 'panel', 'static', 'tests', 'deploy/requirements.lock', 'deploy/settings.xray.example.json', 'deploy/vps-dashboard-xray.service']
SKIP_PARTS = {'__pycache__', 'node_modules'}
MARK_BEGIN, MARK_END = '# JOYSOL MONITOR BEGIN', '# JOYSOL MONITOR END'
GATE_LINES = ('auth_request /_joysol_ws_gate;\n        auth_request_set $joysol_verified_client $upstream_http_x_verified_client_ip;\n'
              '        proxy_set_header X-Forwarded-For $joysol_verified_client;\n        ')

profile = site_profile.load()
R = profile['remote']
BASE = profile['base_path']
STAMP = time.strftime('%Y%m%d%H%M%S')
_db_spec = importlib.util.spec_from_file_location('panel_db', SRC / 'panel/db.py')
_db = importlib.util.module_from_spec(_db_spec)
_db_spec.loader.exec_module(_db)
SCHEMA = _db.MIGRATIONS[-1][0]
RELEASE = posixpath.join(R['install_dir'], 'releases', STAMP)
BACKUP = posixpath.join(R['backup_dir'], 'joysol-dashboard-backup-' + STAMP)
CHECK_ONLY = '--check-only' in sys.argv
SINGBOX = (profile.get('settings', {}).get('singbox') or {}).get('enabled', True) is not False
XRAY = (profile.get('settings', {}).get('xray') or {}).get('instances') or []
MODE = {(True, False): 'sing-box', (False, True): 'Xray', (True, True): 'sing-box + Xray'}.get((SINGBOX, bool(XRAY)))
DEPS = 'import flask, gunicorn, grpc, google.protobuf'


def log(message):
    print('[deploy] ' + message, flush=True)


def access_secrets():
    """Local private export is the source of truth for panel secrets (unchanged from v1)."""
    private = ROOT / 'exports/dashboard-access.json'
    if private.exists():
        access = json.loads(private.read_text())
    else:
        access = {'url': profile['panel_url'], 'username': 'admin', 'password': secrets.token_urlsafe(24),
                  'origin_guard': secrets.token_hex(32), 'api_secret': secrets.token_urlsafe(40), 'salt': secrets.token_hex(16)}
        private.write_text(json.dumps(access, indent=2) + '\n')
        private.chmod(0o600)
    if access.get('url') != profile['panel_url']:
        access['url'] = profile['panel_url']
        private.write_text(json.dumps(access, indent=2) + '\n')
        private.chmod(0o600)
    for name, text in (('esa-origin-guard.txt', access['origin_guard'] + '\n'),
                       ('dashboard-login.txt', '地址：' + access['url'] + '\n用户名：' + access['username'] + '\n密码：' + access['password'] + '\n')):
        path = ROOT / 'exports' / name
        path.write_text(text)
        path.chmod(0o600)
    return access


def server_block(text, port, host):
    """(start, end) of the nginx server{} block with `listen <port> ssl` and `server_name <host>`."""
    for match in re.finditer(r'server\s*\{', text):
        depth, i = 0, match.end() - 1
        while i < len(text):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = text[match.start():i + 1]
        if re.search(r'listen\s+(\[::\]:)?%d\s+ssl' % port, block) and re.search(r'server_name\s+[^;]*\b%s\b' % re.escape(host), block):
            return match.start(), i + 1
    raise RuntimeError('未找到 listen %d ssl + server_name %s 的 Nginx server 块；拒绝修改' % (port, host))


def nginx_block(gate_header):
    var = '$http_' + gate_header.lower().replace('-', '_')
    panel = profile['panel_listen']
    common = ('        proxy_set_header Host $host;\n        proxy_set_header X-Dashboard-Client-IP $remote_addr;\n'
              '        proxy_set_header X-Forwarded-Proto https;\n        access_log off;\n')
    return ('\n    %s\n' % MARK_BEGIN +
            '    location = %s { return 302 %s/; }\n' % (BASE, BASE) +
            '    location ^~ %s/api/stream {\n        proxy_pass http://%s;\n        proxy_http_version 1.1;\n' % (BASE, panel) +
            '        proxy_set_header Connection "";\n        proxy_buffering off;\n        proxy_cache off;\n        proxy_read_timeout 1h;\n' + common +
            '    }\n' +
            '    location ^~ %s/ {\n        proxy_pass http://%s;\n' % (BASE, panel) + common + '    }\n' +
            '    location = /_joysol_ws_gate {\n        internal;\n        proxy_pass http://%s/internal/gate;\n' % panel +
            '        proxy_pass_request_body off;\n        proxy_set_header Content-Length "";\n'
            '        proxy_set_header X-Origin-Guard %s;\n        proxy_set_header X-ESA-Client-IP "";\n' % var +
            '        proxy_set_header X-Origin-XFF $http_x_forwarded_for;\n    }\n' +
            '    %s\n' % MARK_END)


def render_nginx(text, gate, gate_header, ws_ports):
    if MARK_BEGIN in text:
        text = text[:text.index(MARK_BEGIN)].rstrip() + '\n' + text[text.index(MARK_END) + len(MARK_END):].lstrip('\n')
    start, end = server_block(text, profile['origin_tls_port'], profile['host'])
    block = text[start:end]
    listen = re.search(r'listen\s+(\[::\]:)?%d\s+ssl[^\n]*\n' % profile['origin_tls_port'], block)
    block = block[:listen.end()] + nginx_block(gate_header) + block[listen.end():]
    gated = []
    for port in ws_ports:
        needle = 'proxy_pass http://127.0.0.1:%d;' % port
        pos = block.find(needle)
        while pos >= 0:
            location = block.rfind('location', 0, pos)
            segment = block[location:pos]
            if gate and 'auth_request /_joysol_ws_gate;' not in segment:
                block = block[:pos] + GATE_LINES + block[pos:]
                pos += len(GATE_LINES)
            if 'auth_request /_joysol_ws_gate;' in block[location:pos + len(needle)]:
                gated.append(port)
            pos = block.find(needle, pos + len(needle))
    return text[:start] + block + text[end:], sorted(set(gated))


def unit_file():
    after = ' '.join(([R['singbox_service']] if SINGBOX else []) + [i['service'] for i in XRAY])
    if SINGBOX:
        # The xtables lock lives on tmpfs: recreate it before start so a reboot never blocks the panel.
        firewall = ('ExecStartPre=+/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/joysol-dashboard.conf\n', ' -/run/xtables.lock',
                    '# NET_ADMIN/NET_RAW: sing-box bans (iptables). DAC_READ_SEARCH: read core configs for topology and probes; read-only.\n'
                    'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_DAC_READ_SEARCH')
    else:
        firewall = ('', '', '# Xray only: read-only statistics, no firewall privileges. DAC_READ_SEARCH: read the Xray config.\n'
                    'CapabilityBoundingSet=CAP_DAC_READ_SEARCH')
    return '''[Unit]
Description=Joysol node statistics and access panel
After=network.target {after} systemd-tmpfiles-setup.service
[Service]
User=root
WorkingDirectory={install}/current
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=JOYSOL_CONFIG_DIR={config}
{pre}ExecStart={install}/venv/bin/python -m gunicorn --bind {panel} --workers 1 --worker-class gthread --threads 16 --timeout 60 --graceful-timeout 10 --access-logfile /dev/null --error-logfile - app:app
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={data}{lock}
{caps}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
[Install]
WantedBy=multi-user.target
'''.format(after=after, install=R['install_dir'], config=R['config_dir'], panel=profile['panel_listen'], data=R['data_dir'],
           pre=firewall[0], lock=firewall[1], caps=firewall[2])


def healthy(health):
    if not health or health['schema'] != str(SCHEMA):
        return False
    if SINGBOX and not (health['health']['connected'] and health['inbounds']):
        return False
    instances = (health.get('xray') or {}).get('instances') or []
    return not XRAY or (len(instances) == len(XRAY) and all(i['connected'] for i in instances))


def main():
    if not (SRC / 'static/app/index.html').exists():
        raise SystemExit('前端未构建：cd dashboard/web && npm run build')
    access = access_secrets()
    client, _ = r.connect()
    sftp = None

    def run(cmd, timeout=180, check=True):
        result = r.m.execute(client, cmd, timeout)
        if check and result['rc']:
            raise RuntimeError('远端步骤失败 (exit %s): %s' % (result['rc'], (result['stderr'] or result['stdout'])[-1500:]))
        return result

    def read(path):
        with sftp.open(path) as handle:
            return handle.read().decode()

    def write(path, text, mode=0o600):
        tmp = path + '.joysol-new'
        with sftp.open(tmp, 'w') as handle:
            handle.write(text)
        sftp.chmod(tmp, mode)
        sftp.posix_rename(tmp, path)

    switched = False
    try:
        sftp = client.open_sftp()
        # 1. Read-only preflight.
        log('预检（只读）；部署模式：%s' % MODE)
        q = shlex.quote
        if not MODE:
            raise RuntimeError('site.json 未启用任何监控来源：至少保留 sing-box 或配置 xray.instances')
        if '--gate' in sys.argv and not SINGBOX:
            raise RuntimeError('--gate 只适用于 sing-box 回环 WS 入站；Xray 入口当前只读监控')
        inst, data = R['install_dir'], R['data_dir']
        probes = ['echo auth=$(nginx -V 2>&1 | grep -c http_auth_request_module || true)',
                  'd=%s; while [ ! -e "$d" ]; do d=$(dirname "$d"); done; echo free=$(df -P "$d" | tail -1 | awk \'{print $4}\')' % q(data),
                  'echo venv=$(test -x %s/venv/bin/python && echo 1 || echo 0)' % q(inst),
                  'echo deps=$(%s/venv/bin/python -c %s 2>/dev/null && echo 1 || echo 0)' % (q(inst), q(DEPS)),
                  'echo py310=$(python3 -c "import sys;print(int(sys.version_info>=(3,10)))" 2>/dev/null || echo 0)',
                  'echo ledger=$(test -e %s/stats.sqlite3 && echo 1 || echo 0)' % q(data),
                  'echo nginx_conf=$(test -s %s && echo 1 || echo 0)' % q(R['nginx_conf']),
                  'echo services=$(systemctl is-active %s nginx.service | tr "\\n" ",")' % ' '.join(
                      q(s) for s in ([R['singbox_service']] if SINGBOX else []) + [R['service']])]
        if SINGBOX:
            probes.append('echo singbox_conf=$(test -s %s && echo 1 || echo 0)' % q(R['singbox_config']))
        for i in XRAY:
            port = i['api_address'].rsplit(':', 1)[1]
            probes.append('pid=$(systemctl show -p MainPID --value %s); echo xray_%s=$(systemctl is-active %s)/$(test -r %s && echo 1 || echo 0)/'
                          '$(ss -ltnH "sport = :%s" | grep -cE "(127\\.0\\.0\\.1|\\[::1\\]):%s")/$(readlink /proc/$pid/exe 2>/dev/null || echo -)' % (
                              q(i['service']), i['id'], q(i['service']), q(i['config_path']), port, port))
        pre = dict(line.split('=', 1) for line in run('; '.join(probes))['stdout'].splitlines() if '=' in line)
        free_kb = int(pre['free'])
        log('auth_request 模块：%s；可用空间 %.1f GiB；服务：%s' % ('有' if pre['auth'] != '0' else '无', free_kb / 1048576, pre['services'].strip(',')))
        if free_kb < 1024 * 1024:
            raise RuntimeError('可用空间不足 1 GiB，停止部署')
        if pre['nginx_conf'] != '1':
            raise RuntimeError('Nginx 配置 %s 不存在；面板只挂在已有 HTTPS server 块下' % R['nginx_conf'])
        if SINGBOX and pre['singbox_conf'] != '1':
            raise RuntimeError('sing-box 配置不存在；只装 Xray 时在 site.json 设 settings.singbox.enabled=false')
        if pre['venv'] != '1' and pre['py310'] != '1':
            raise RuntimeError('远端 python3 低于 3.10，无法创建面板虚拟环境')
        xray_binary = ''
        for i in XRAY:
            active, readable, listening, binary = pre['xray_' + i['id']].split('/', 3)
            log('Xray %s：服务 %s；配置%s可读；回环 StatsService %s' % (i['id'], active, '' if readable == '1' else '不', '已监听' if listening != '0' else '未监听'))
            if active != 'active' or readable != '1' or listening == '0':
                raise RuntimeError('Xray 实例 %s 未就绪；先确认服务与配置，再运行 scripts/enable-xray-stats.py --instance %s' % (i['id'], i['id']))
            xray_binary = xray_binary or (binary if binary != '-' else '')
        if pre['venv'] != '1' or pre['deps'] != '1':
            log('面板虚拟环境%s；将按 dashboard/deploy/requirements.lock 安装缺少的依赖' % ('不存在，将新建' if pre['venv'] != '1' else '缺少依赖'))
        sb, ws_ports, gate, singbox_changes = {}, [], False, False
        ng_current = read(R['nginx_conf'])
        if SINGBOX:
            sb = json.loads(read(R['singbox_config']))
            gate = '--gate' in sys.argv or 'auth_request /_joysol_ws_gate;' in ng_current
            ws_ports = sorted({i['listen_port'] for i in sb.get('inbounds', []) if (i.get('transport') or {}).get('type') in ('ws', 'httpupgrade')
                               and i.get('listen') in ('127.0.0.1', '::1', 'localhost') and i.get('listen_port')})
            log('发现 %d 个入站、%d 个出站；回环 WS 入站端口 %s；CDN 网关%s' % (len(sb.get('inbounds', [])), len(sb.get('outbounds', [])), ws_ports, '启用' if gate else '未启用'))
        settings = site_profile_settings(gate)
        candidate_ng, gated = render_nginx(ng_current, gate, settings['gate']['origin_header'], ws_ports)
        if gate and gated != ws_ports:
            raise RuntimeError('部分 WS 入站未能挂上回源网关：%s' % sorted(set(ws_ports) - set(gated)))
        if SINGBOX:
            api_host, api_port = profile['singbox_api_listen'].rsplit(':', 1)
            service = {'type': 'api', 'tag': 'joysol-monitor-api', 'listen': api_host, 'listen_port': int(api_port), 'secret': access['api_secret'], 'dashboard': {'enabled': False}}
            sb_new = dict(sb)
            sb_new['services'] = [x for x in sb.get('services', []) if x.get('tag') != 'joysol-monitor-api'] + [service]
            singbox_changes = json.dumps(sb_new, sort_keys=True) != json.dumps(sb, sort_keys=True)
            log('sing-box 配置%s' % ('需要更新 API 服务（将重启 sing-box，会短暂断线）' if singbox_changes else '无需改动（不重启代理）'))
        if XRAY:
            log('Xray %d 个实例只读接入，不修改、不重启 Xray' % len(XRAY))
        if CHECK_ONLY:
            log('--check-only：未做任何修改')
            return

        # 2. Upload release.
        log('上传版本 %s' % RELEASE)
        run('install -d -m 755 %s' % q(RELEASE))
        count = 0
        for name in SHIP:
            base = SRC / name
            files = [base] if base.is_file() else [f for f in base.rglob('*') if f.is_file()]
            for f in files:
                rel = f.relative_to(SRC)
                if SKIP_PARTS & set(rel.parts) or f.suffix == '.pyc':
                    continue
                target = posixpath.join(RELEASE, *rel.parts)
                run('install -d -m 755 %s' % q(posixpath.dirname(target)))
                sftp.put(str(f), target)
                count += 1
        log('已上传 %d 个文件' % count)
        if pre['venv'] != '1':
            run('install -d -m 755 %s && python3 -m venv %s/venv' % (q(inst), q(inst)), 300)
        if pre['venv'] != '1' or pre['deps'] != '1':
            # Pinned lock only when something is missing; an existing complete venv is never upgraded.
            run('%s/venv/bin/python -m pip install -q --disable-pip-version-check -r %s/deploy/requirements.lock && %s/venv/bin/python -c %s' % (
                q(inst), q(RELEASE), q(inst), q(DEPS)), 900)
            log('依赖已安装')
        geo_dir = posixpath.join(data, 'geo')
        run('install -d -m 700 %s %s' % (q(data), q(geo_dir)))
        for f in sorted((SRC / 'deploy/geo').glob('*.xdb')):
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            remote_digest = run('sha256sum %s 2>/dev/null | cut -c1-64' % q(posixpath.join(geo_dir, f.name)), check=False)['stdout'].strip()
            if remote_digest != digest:
                sftp.put(str(f), posixpath.join(geo_dir, f.name) + '.part')
                run('mv %s %s && chmod 600 %s' % (q(posixpath.join(geo_dir, f.name) + '.part'), q(posixpath.join(geo_dir, f.name)), q(posixpath.join(geo_dir, f.name))))
                log('定位库已更新：%s' % f.name)

        # 3. Remote tests and migration rehearsal on the live ledger (read-only, in memory).
        log('远端运行测试套件')
        modules = 'test_panel test_media_probes test_xray_dashboard test_alert_sources test_engine_modes'
        env = 'JOYSOL_TEST=1'
        if XRAY and xray_binary:
            # Native wire test: the server's own Xray binary on loopback with a throwaway config.
            modules += ' test_xray'
            env += ' XRAY_TEST_BINARY=' + q(xray_binary)
        tests = run('cd %s/tests && %s timeout 300 %s/venv/bin/python -B -W ignore -m unittest %s 2>&1 | tail -3' % (q(RELEASE), env, q(inst), modules), 600)['stdout']
        if 'OK' not in tests:
            raise RuntimeError('远端测试失败：\n' + tests)
        log('测试：' + ' '.join(tests.split()))
        if pre['ledger'] == '1':
            rehearsal = json.loads(run("cd %s && %s/venv/bin/python -B -c \"import json;from panel import db;print(json.dumps(db.dry_run('%s/stats.sqlite3')))\"" % (
                q(RELEASE), q(inst), data), 600)['stdout'].strip().splitlines()[-1])
            log('迁移演练：%s' % rehearsal)
            if not rehearsal['rollups_match']:
                raise RuntimeError('迁移演练汇总不一致，停止部署')
        else:
            log('首次安装：无现有账本，跳过迁移演练')

        # 4. Full backup before any change (a fresh host has less to back up).
        log('备份到 %s' % BACKUP)
        unit_path = '/etc/systemd/system/' + R['service']
        run('set -e; install -d -m 700 {b}; if [ -e {cfg} ]; then cp -a {cfg} {b}/config-dir; fi; if [ -e {unit} ]; then cp -a {unit} {b}/unit.service; fi; '
            'cp -a {ng} {b}/nginx.conf; {sbcopy}'
            'if [ -L {inst}/current ]; then readlink {inst}/current > {b}/previous-release; elif [ -d {inst} ]; then echo legacy > {b}/previous-release; else echo none > {b}/previous-release; fi; '
            'if [ -d {inst} ]; then tar -C {inst} --exclude=./venv --exclude=./releases -cf {b}/code.tar .; fi'.format(
                b=q(BACKUP), cfg=q(R['config_dir']), unit=q(unit_path), ng=q(R['nginx_conf']), inst=q(inst),
                sbcopy='cp -a %s %s/sing-box.json; ' % (q(R['singbox_config']), q(BACKUP)) if SINGBOX else ''))
        for ledger in ('stats.sqlite3', 'xray-traffic.sqlite3'):
            run("if [ -e {d}/{n} ]; then python3 -c \"import sqlite3;s=sqlite3.connect('file:{d}/{n}?mode=ro',uri=True);d=sqlite3.connect('{b}/{n}');s.backup(d);d.close();s.close()\" && chmod 600 {b}/{n}; fi".format(
                d=data, n=ledger, b=BACKUP), 600)

        # 5. Configuration.
        secrets_cfg = {k: access[k] for k in ('username', 'salt', 'api_secret', 'origin_guard')}
        secrets_cfg['password_hash'] = hashlib.pbkdf2_hmac('sha256', access['password'].encode(), bytes.fromhex(access['salt']), 600000).hex()
        # Optional extra panel accounts from the private export: [{"username", "password", "salt"?}].
        users = []
        for extra in access.get('extra_users', []):
            if extra['username'] == access['username']:
                raise RuntimeError('额外账户不能与主账户同名')
            salt = extra.get('salt') or secrets.token_hex(16)
            users.append({'username': extra['username'], 'salt': salt,
                          'password_hash': hashlib.pbkdf2_hmac('sha256', extra['password'].encode(), bytes.fromhex(salt), 600000).hex()})
        if users:
            secrets_cfg['users'] = users
        # Legacy keys keep v1 code and older maintenance scripts working on rollback.
        secrets_cfg.update(public_origin=profile['origin'], esa_gate_enabled=gate)
        run('install -d -m 700 %s' % q(R['config_dir']))
        write(posixpath.join(R['config_dir'], 'config.json'), json.dumps(secrets_cfg))
        write(posixpath.join(R['config_dir'], 'settings.json'), json.dumps(settings, ensure_ascii=False, indent=2))
        if SINGBOX:
            write('/etc/tmpfiles.d/joysol-dashboard.conf', 'f /run/xtables.lock 0600 root root -\n', 0o644)
        write(unit_path, unit_file(), 0o644)
        if singbox_changes:
            write(R['singbox_config'] + '.dashboard-candidate', json.dumps(sb_new, indent=2) + '\n')
            run('%s check -c %s' % (q(R['singbox_binary']), q(R['singbox_config'] + '.dashboard-candidate')))
        write(R['nginx_conf'], candidate_ng, stat.S_IMODE(sftp.stat(R['nginx_conf']).st_mode))
        try:
            run('nginx -t 2>&1')
        except Exception:
            run('cp -a %s/nginx.conf %s' % (q(BACKUP), q(R['nginx_conf'])))
            raise

        # 6. Switch and health check.
        switched = True
        log('切换到新版本（仅重启面板服务）')
        run('set -e; %sln -sfn %s %s/current.new && mv -T %s/current.new %s/current; systemctl daemon-reload; '
            'systemctl enable %s >/dev/null 2>&1; systemctl restart %s' % ('touch /run/xtables.lock; ' if SINGBOX else '', q(RELEASE), q(inst), q(inst), q(inst), q(R['service']), q(R['service'])))
        if singbox_changes:
            run('cp %s %s && systemctl restart %s' % (q(R['singbox_config'] + '.dashboard-candidate'), q(R['singbox_config']), q(R['singbox_service'])))
        health = None
        panel_host, panel_port = profile['panel_listen'].rsplit(':', 1)
        for _ in range(40):
            res = run('curl -s --max-time 3 http://%s:%s/internal/health' % (panel_host, panel_port), check=False)
            if res['rc'] == 0 and res['stdout'].startswith('{'):
                health = json.loads(res['stdout'])
                if healthy(health):
                    break
            time.sleep(1.5)
        if not healthy(health):
            raise RuntimeError('健康检查未通过：%s' % health)
        login = run('curl -s -o /dev/null -w "%%{http_code}" http://%s:%s%s/login' % (panel_host, panel_port, BASE))['stdout']
        if login != '200':
            raise RuntimeError('登录页返回 %s' % login)
        run('systemctl reload nginx')
        parts = ['sing-box 采集正常，入站 %d' % health['inbounds']] if SINGBOX else []
        parts += ['Xray %d 个实例采样正常' % len(XRAY)] if XRAY else []
        log('健康：%s；schema v%s；定位库 %s；本次迁移 %s' % ('，'.join(parts), health['schema'], ', '.join(health['geo']), health['migrations']))
        log('完成。备份保留在 %s' % BACKUP)
    except Exception:
        if switched:
            log('失败，自动回滚到备份')
            rollback(run, q)
        raise
    finally:
        if sftp:
            sftp.close()
        client.close()


def rollback(run, q):
    unit_path = '/etc/systemd/system/' + R['service']
    data = R['data_dir']
    failed = posixpath.join(BACKUP, 'failed-ledger')
    run('set +e; systemctl stop {svc}; install -d -m 700 {f}; for n in stats.sqlite3 xray-traffic.sqlite3; do for s in "" -wal -shm; do [ -e {d}/$n$s ] && mv {d}/$n$s {f}/; done; '
        '[ -e {b}/$n ] && cp -a {b}/$n {d}/$n; done; if [ -e {b}/unit.service ]; then cp -a {b}/unit.service {unit}; else systemctl disable {svc}; rm -f {unit}; fi; '
        'rm -rf {cfg}.rollback; mv {cfg} {cfg}.rollback; [ -e {b}/config-dir ] && cp -a {b}/config-dir {cfg}; '
        'cp -a {b}/nginx.conf {ng}; prev=$(cat {b}/previous-release); if [ "$prev" = none ]; then rm -f {inst}/current; elif [ "$prev" != legacy ]; then ln -sfn "$prev" {inst}/current; fi; '
        '[ -e {b}/sing-box.json ] && cp -a {b}/sing-box.json {sb}; systemctl daemon-reload; [ -e {unit} ] && systemctl start {svc}; nginx -t && systemctl reload nginx'.format(
            svc=q(R['service']), f=q(failed), d=q(data), b=q(BACKUP), unit=q(unit_path), cfg=q(R['config_dir']), ng=q(R['nginx_conf']),
            inst=q(R['install_dir']), sb=q(R.get('singbox_config', '/nonexistent'))), 300, check=False)
    log('已回滚；失败版本的账本保存在 %s' % failed)


def site_profile_settings(gate):
    values = json.loads(json.dumps(profile.get('settings', {})))
    values.setdefault('site', {}).update(public_url=profile['origin'], base_path=BASE)
    values.setdefault('paths', {})['data_dir'] = R['data_dir']
    values.setdefault('singbox', {})['enabled'] = SINGBOX
    if SINGBOX:
        values['singbox'].update(api_address=profile['singbox_api_listen'], config_path=R['singbox_config'], binary=R['singbox_binary'])
    # Service alerts follow the mode: drop sing-box when it is not installed, watch every Xray instance.
    alerts = values.setdefault('alerts', {})
    services = [s for s in alerts.get('services', ['nginx.service']) if SINGBOX or s != R.get('singbox_service', 'sing-box.service')]
    alerts['services'] = services + [i['service'] for i in XRAY if i['service'] not in services]
    values.setdefault('gate', {}).update(enabled=gate)
    values['gate'].setdefault('origin_header', 'X-Joysol-Origin')
    # CDN edge → origin TLS port: its TCP sockets measure the edge leg in the network view.
    values.setdefault('network', {}).setdefault('edge_ports', [profile['origin_tls_port']])
    return values


if __name__ == '__main__':
    main()
