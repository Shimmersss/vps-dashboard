"""Deploy the node panel (v2) from dashboard/ using dashboard/deploy/site.json; prints no secrets.

    .venv/bin/python -B scripts/deploy-dashboard.py [--gate] [--check-only]

Order: read-only preflight -> upload release -> remote tests + in-memory migration rehearsal on the
live ledger -> full backup -> write config/unit/nginx -> switch + health check -> nginx reload.
Any failure after the switch restores the backup automatically. sing-box restarts only if its API
service block actually changes (normally never). --gate enables the CDN WebSocket gate; only use it
once the CDN origin header is saved and verified (see docs/dashboard.md).
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
SHIP = ['app.py', 'panel', 'static', 'tests']
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
    return '''[Unit]
Description=Joysol node statistics and access panel
After=network.target {sb} systemd-tmpfiles-setup.service
[Service]
User=root
WorkingDirectory={install}/current
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=JOYSOL_CONFIG_DIR={config}
ExecStartPre=+/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/joysol-dashboard.conf
ExecStart={install}/venv/bin/python -m gunicorn --bind {panel} --workers 1 --worker-class gthread --threads 16 --timeout 60 --graceful-timeout 10 --access-logfile /dev/null --error-logfile - app:app
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={data} -/run/xtables.lock
# DAC_READ_SEARCH: read the sing-box config (owned by the sing-box user) for topology and probes; read-only.
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_DAC_READ_SEARCH
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
[Install]
WantedBy=multi-user.target
'''.format(sb=R['singbox_service'], install=R['install_dir'], config=R['config_dir'], panel=profile['panel_listen'], data=R['data_dir'])


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
        log('预检（只读）')
        q = shlex.quote
        out = run('set -e; test -s %s; test -x %s/venv/bin/python; (nginx -V 2>&1 | grep -c http_auth_request_module || true); '
                  'df -P %s | tail -1 | awk \'{print $4}\'; systemctl is-active %s %s nginx.service || true' % (
                      q(R['singbox_config']), q(R['install_dir']), q(R['data_dir']), q(R['singbox_service']), q(R['service'])))['stdout'].split()
        free_kb = int(out[1])
        log('auth_request 模块：%s；可用空间 %.1f GiB；服务：%s' % ('有' if out[0] != '0' else '无', free_kb / 1048576, ' '.join(out[2:])))
        if free_kb < 1024 * 1024:
            raise RuntimeError('可用空间不足 1 GiB，停止部署')
        sb = json.loads(read(R['singbox_config']))
        ng_current = read(R['nginx_conf'])
        gate = '--gate' in sys.argv or 'auth_request /_joysol_ws_gate;' in ng_current
        ws_ports = sorted({i['listen_port'] for i in sb.get('inbounds', []) if (i.get('transport') or {}).get('type') in ('ws', 'httpupgrade')
                           and i.get('listen') in ('127.0.0.1', '::1', 'localhost') and i.get('listen_port')})
        log('发现 %d 个入站、%d 个出站；回环 WS 入站端口 %s；CDN 网关%s' % (len(sb.get('inbounds', [])), len(sb.get('outbounds', [])), ws_ports, '启用' if gate else '未启用'))
        settings = site_profile_settings(gate)
        candidate_ng, gated = render_nginx(ng_current, gate, settings['gate']['origin_header'], ws_ports)
        if gate and gated != ws_ports:
            raise RuntimeError('部分 WS 入站未能挂上回源网关：%s' % sorted(set(ws_ports) - set(gated)))
        api_host, api_port = profile['singbox_api_listen'].rsplit(':', 1)
        service = {'type': 'api', 'tag': 'joysol-monitor-api', 'listen': api_host, 'listen_port': int(api_port), 'secret': access['api_secret'], 'dashboard': {'enabled': False}}
        sb_new = dict(sb)
        sb_new['services'] = [x for x in sb.get('services', []) if x.get('tag') != 'joysol-monitor-api'] + [service]
        singbox_changes = json.dumps(sb_new, sort_keys=True) != json.dumps(sb, sort_keys=True)
        log('sing-box 配置%s' % ('需要更新 API 服务（将重启 sing-box，会短暂断线）' if singbox_changes else '无需改动（不重启代理）'))
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
        geo_dir = posixpath.join(R['data_dir'], 'geo')
        run('install -d -m 700 %s' % q(geo_dir))
        for f in sorted((SRC / 'deploy/geo').glob('*.xdb')):
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            remote_digest = run('sha256sum %s 2>/dev/null | cut -c1-64' % q(posixpath.join(geo_dir, f.name)), check=False)['stdout'].strip()
            if remote_digest != digest:
                sftp.put(str(f), posixpath.join(geo_dir, f.name) + '.part')
                run('mv %s %s && chmod 600 %s' % (q(posixpath.join(geo_dir, f.name) + '.part'), q(posixpath.join(geo_dir, f.name)), q(posixpath.join(geo_dir, f.name))))
                log('定位库已更新：%s' % f.name)

        # 3. Remote tests and migration rehearsal on the live ledger (read-only, in memory).
        log('远端运行测试套件')
        tests = run('cd %s/tests && JOYSOL_TEST=1 timeout 300 %s/venv/bin/python -B -W ignore -m unittest test_panel 2>&1 | tail -3' % (q(RELEASE), q(R['install_dir'])), 600)['stdout']
        if 'OK' not in tests:
            raise RuntimeError('远端测试失败：\n' + tests)
        log('测试：' + ' '.join(tests.split()))
        rehearsal = json.loads(run("cd %s && %s/venv/bin/python -B -c \"import json;from panel import db;print(json.dumps(db.dry_run('%s/stats.sqlite3')))\"" % (
            q(RELEASE), q(R['install_dir']), R['data_dir']), 600)['stdout'].strip().splitlines()[-1])
        log('迁移演练：%s' % rehearsal)
        if not rehearsal['rollups_match']:
            raise RuntimeError('迁移演练汇总不一致，停止部署')

        # 4. Full backup before any change.
        log('备份到 %s' % BACKUP)
        unit_path = '/etc/systemd/system/' + R['service']
        run('set -e; install -d -m 700 {b}; cp -a {cfg} {b}/config-dir; cp -a {unit} {b}/unit.service; cp -a {ng} {b}/nginx.conf; cp -a {sb} {b}/sing-box.json; '
            'readlink {inst}/current > {b}/previous-release 2>/dev/null || echo legacy > {b}/previous-release; '
            'tar -C {inst} --exclude=./venv --exclude=./releases -cf {b}/code.tar .'.format(
                b=q(BACKUP), cfg=q(R['config_dir']), unit=q(unit_path), ng=q(R['nginx_conf']), sb=q(R['singbox_config']), inst=q(R['install_dir'])))
        run("python3 -c \"import sqlite3;s=sqlite3.connect('file:%s/stats.sqlite3?mode=ro',uri=True);d=sqlite3.connect('%s/stats.sqlite3');s.backup(d);d.close();s.close()\" && chmod 600 %s/stats.sqlite3" % (
            R['data_dir'], BACKUP, BACKUP), 600)

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
        run('set -e; touch /run/xtables.lock; ln -sfn %s %s/current.new && mv -T %s/current.new %s/current; systemctl daemon-reload; '
            'systemctl enable %s >/dev/null 2>&1; systemctl restart %s' % (q(RELEASE), q(R['install_dir']), q(R['install_dir']), q(R['install_dir']), q(R['service']), q(R['service'])))
        if singbox_changes:
            run('cp %s %s && systemctl restart %s' % (q(R['singbox_config'] + '.dashboard-candidate'), q(R['singbox_config']), q(R['singbox_service'])))
        health = None
        panel_host, panel_port = profile['panel_listen'].rsplit(':', 1)
        for _ in range(40):
            res = run('curl -s --max-time 3 http://%s:%s/internal/health' % (panel_host, panel_port), check=False)
            if res['rc'] == 0 and res['stdout'].startswith('{'):
                health = json.loads(res['stdout'])
                if health['health']['connected']:
                    break
            time.sleep(1.5)
        if not health or not health['health']['connected'] or health['schema'] != str(SCHEMA) or not health['inbounds']:
            raise RuntimeError('健康检查未通过：%s' % health)
        login = run('curl -s -o /dev/null -w "%%{http_code}" http://%s:%s%s/login' % (panel_host, panel_port, BASE))['stdout']
        if login != '200':
            raise RuntimeError('登录页返回 %s' % login)
        run('systemctl reload nginx')
        log('健康：采集正常；schema v%s；入站 %d；定位库 %s；本次迁移 %s' % (health['schema'], health['inbounds'], ', '.join(health['geo']), health['migrations']))
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
    run('set +e; systemctl stop {svc}; install -d -m 700 {f}; for s in "" -wal -shm; do [ -e {d}/stats.sqlite3$s ] && mv {d}/stats.sqlite3$s {f}/; done; '
        'cp -a {b}/stats.sqlite3 {d}/stats.sqlite3; cp -a {b}/unit.service {unit}; rm -rf {cfg}.rollback; mv {cfg} {cfg}.rollback; cp -a {b}/config-dir {cfg}; '
        'cp -a {b}/nginx.conf {ng}; prev=$(cat {b}/previous-release); if [ "$prev" != legacy ]; then ln -sfn "$prev" {inst}/current; fi; '
        'cp -a {b}/sing-box.json {sb}; systemctl daemon-reload; systemctl start {svc}; nginx -t && systemctl reload nginx'.format(
            svc=q(R['service']), f=q(failed), d=q(data), b=q(BACKUP), unit=q(unit_path), cfg=q(R['config_dir']), ng=q(R['nginx_conf']),
            inst=q(R['install_dir']), sb=q(R['singbox_config'])), 300, check=False)
    log('已回滚；失败版本的账本保存在 %s' % failed)


def site_profile_settings(gate):
    values = json.loads(json.dumps(profile.get('settings', {})))
    values.setdefault('site', {}).update(public_url=profile['origin'], base_path=BASE)
    values.setdefault('paths', {})['data_dir'] = R['data_dir']
    values.setdefault('singbox', {}).update(api_address=profile['singbox_api_listen'], config_path=R['singbox_config'], binary=R['singbox_binary'])
    values.setdefault('gate', {}).update(enabled=gate)
    values['gate'].setdefault('origin_header', 'X-Joysol-Origin')
    # CDN edge → origin TLS port: its TCP sockets measure the edge leg in the network view.
    values.setdefault('network', {}).setdefault('edge_ports', [profile['origin_tls_port']])
    return values


if __name__ == '__main__':
    main()
