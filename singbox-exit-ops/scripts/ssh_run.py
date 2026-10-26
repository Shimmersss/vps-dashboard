#!/usr/bin/env python3
"""Project-local SSH runner. Credentials are loaded without executing .env."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

import paramiko

ROOT = Path(__file__).resolve().parents[2]


def load_settings(path):
    values = {}
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('.env 权限必须为 0600')
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator:
                raise ValueError('.env 必须使用 KEY=VALUE 格式')
            # JSON string values preserve all special characters; never shell-evaluate.
            value = value.strip()
            values[key.strip()] = json.loads(value) if value.startswith('"') else value
    result = {key: os.environ.get(key, values.get(key, default)) for key, default in {
        'SSH_HOST': '', 'SSH_PORT': '22',
        'SSH_USER': 'root', 'SSH_RUN_PASSWORD': '',
    }.items()}
    result['SSH_PORT'] = int(result['SSH_PORT'])
    if not result['SSH_HOST'] or not result['SSH_USER'] or not result['SSH_RUN_PASSWORD']:
        raise ValueError('缺少 SSH_HOST、SSH_USER 或 SSH_RUN_PASSWORD')
    if not 1 <= result['SSH_PORT'] <= 65535:
        raise ValueError('SSH_PORT 超出范围')
    return result


def execute(client, command, timeout):
    # Drain both streams together to avoid SSH channel window deadlocks.
    channel = client.get_transport().open_session(timeout=timeout)
    channel.settimeout(timeout)
    channel.exec_command(command)
    out, err = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('远程命令超时；请只读核实远程进程状态后再重试')
            if channel.recv_ready():
                out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():
                err.extend(channel.recv_stderr(65536))
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(0.01)
        return {'rc': channel.recv_exit_status(), 'stdout': out.decode('utf-8', 'replace'),
                'stderr': err.decode('utf-8', 'replace')}
    finally:
        channel.close()


class AcceptNewKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        print(f'首次连接记录主机密钥（TOFU，未经独立核验）: SHA256:{fingerprint}', file=sys.stderr)
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(ROOT / '.ssh' / 'known_hosts'))
        (ROOT / '.ssh' / 'known_hosts').chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-local', action='store_true')
    mode.add_argument('--test-connection', action='store_true')
    mode.add_argument('--commands', type=Path, help='JSON [{"cmd": "...", "timeout": 60}]')
    parser.add_argument('--output', type=Path, help='结果文件，0600；可能含远程敏感信息')
    parser.add_argument('--accept-new-host-key', action='store_true', help='首次连接使用 TOFU 保存新密钥；已知密钥变化仍拒绝')
    args = parser.parse_args()
    settings = load_settings(args.env_file)
    if args.check_local:
        print(json.dumps({'local_check': 'ok', 'host': settings['SSH_HOST'],
                          'port': settings['SSH_PORT'], 'user': settings['SSH_USER'],
                          'credential': 'configured', 'paramiko': paramiko.__version__}, ensure_ascii=False))
        return 0
    specs = [{'cmd': 'hostname; uname -sm', 'timeout': 20}] if args.test_connection else json.loads(args.commands.read_text())
    if not isinstance(specs, list) or not specs:
        raise ValueError('命令清单必须为非空数组')
    for item in specs:
        if not isinstance(item, dict) or not isinstance(item.get('cmd'), str) or not item['cmd'].strip():
            raise ValueError('每条命令必须包含非空 cmd')
        timeout = item.get('timeout', 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError('timeout 必须为正数')
    host_dir = ROOT / '.ssh'
    host_dir.mkdir(mode=0o700, exist_ok=True)
    host_dir.chmod(0o700)
    known_hosts = host_dir / 'known_hosts'
    with paramiko.SSHClient() as client:
        client.load_system_host_keys()
        if known_hosts.exists():
            client.load_host_keys(str(known_hosts))
        if args.accept_new_host_key:
            client.set_missing_host_key_policy(AcceptNewKey())
        client.connect(settings['SSH_HOST'], port=settings['SSH_PORT'], username=settings['SSH_USER'],
                       password=settings['SSH_RUN_PASSWORD'], timeout=20, auth_timeout=20,
                       banner_timeout=20, look_for_keys=False, allow_agent=False)
        results = []
        for index, item in enumerate(specs):
            result = execute(client, item['cmd'], item.get('timeout', 60))
            results.append({'index': index, **result})
            if result['rc'] != 0:
                break  # Never continue from a failed check into a restart.
    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload + '\n')
        print(f'结果已保存：{args.output}')
    else:
        print(payload)
    return 0 if all(item['rc'] == 0 for item in results) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Avoid echoing exceptions that might contain commands or secrets.
        print(f'执行失败（{type(exc).__name__}）；检查连接、主机密钥、凭据与参数。', file=sys.stderr)
        sys.exit(2)
