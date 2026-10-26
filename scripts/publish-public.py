"""Publish a redacted copy of the project to the public GitHub repository.

The private map exports/public-publish.json lists values to replace (domain, server / exit addresses, SSH port,
local paths), files to leave out (glob patterns; internal records such as AGENTS.md / WORKLOG.md stay private)
and the sample addresses tests may keep. Public-facing docs live in public-docs/ and are copied over the
repository root (public-docs/README.md becomes README.md). The copy is built in
outputs/public-repo/ (git-ignored); any leftover private value, unknown public IP or UUID aborts before commit.
Usage: scripts/publish-public.py [--push [--rewrite-history]] [-m message]
--rewrite-history replaces the public history with one fresh commit (force push); use it after something
that should never have been public was published.
"""
import argparse
import fnmatch
import gzip
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / 'exports/public-publish.json'
STAGE = ROOT / 'outputs/public-repo'
PUBLIC_DOCS = 'public-docs/'
IPV4 = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
IPV6 = re.compile(r'(?<![\w:.])(?:[0-9a-fA-F]{1,4}:){1,7}:?(?:[0-9a-fA-F]{1,4})?(?::[0-9a-fA-F]{1,4})*(?![\w:])')
UUID = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)
SAMPLE_UUID = re.compile(r'^0{8}-0{4}-4000-8000-0{11}\d$')


def git(*args, cwd=STAGE, capture=False):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=capture, text=True).stdout


def tracked_files(exclude):
    """Files git would add under the project .gitignore, without keeping a repository in the project."""
    with tempfile.TemporaryDirectory() as tmp:
        git('init', '-q', tmp, cwd=ROOT)
        out = git('--git-dir', tmp + '/.git', '--work-tree', str(ROOT), 'ls-files', '-o', '--exclude-standard', '-z',
                  cwd=ROOT, capture=True)
    return sorted(f for f in out.split('\0') if f and not any(fnmatch.fnmatchcase(f, pat) for pat in exclude))


def mask(value):
    return value[:4] + '…' if len(value) > 4 else '…'


def leaks(text, cfg):
    found = [('private value', mask(old)) for old, _ in cfg['replace'] if old in text]
    for pattern in (IPV4, IPV6):
        for m in set(pattern.findall(text)):
            try:
                addr = ipaddress.ip_address(m)
            except ValueError:
                continue
            if addr.is_global and str(addr) not in cfg['allow_ips'] and m not in cfg['allow_ips'] \
                    and not any(addr in ipaddress.ip_network(n) for n in ('203.0.113.0/24', '198.51.100.0/24', '192.0.2.0/24')):
                found.append(('public IP', mask(m)))
    found += [('uuid', mask(u)) for u in set(UUID.findall(text)) if not SAMPLE_UUID.match(u)]
    return found


def build(cfg):
    if not (STAGE / '.git').exists():
        STAGE.mkdir(parents=True, exist_ok=True)
        git('init', '-q', '-b', 'main')
        git('remote', 'add', 'origin', 'https://github.com/%s.git' % cfg['repo'])
    for item in STAGE.iterdir():
        if item.name != '.git':
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    problems = []
    files = tracked_files(cfg['exclude'])
    # (source, published path): public docs replace the private ones at the repository root.
    pairs = [(rel, rel[len(PUBLIC_DOCS):] if rel.startswith(PUBLIC_DOCS) else rel) for rel in files]
    published = [dst for _, dst in pairs]
    clash = sorted({d for d in published if published.count(d) > 1})
    if clash:
        sys.exit('public-docs 与项目文件重名，请在 exclude 中排除原文件：' + ', '.join(clash))
    for rel, out in pairs:
        src, dst = ROOT / rel, STAGE / out
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        try:
            text = data.decode()
        except UnicodeDecodeError:
            text = None
        if text is not None:
            for old, new in cfg['replace']:
                text = text.replace(old, new)
            dst.write_text(text)
            problems += [(rel, *p) for p in leaks(text, cfg)]
        else:
            dst.write_bytes(data)
            if rel.endswith('.gz'):
                # Pre-compressed assets are rebuilt from the same sources; they must carry no private values either.
                problems += [(rel, *p) for p in leaks(gzip.decompress(data).decode(errors='ignore'), cfg)]
        shutil.copymode(src, dst)
    return files, problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--push', action='store_true', help='commit and push after a clean leak check')
    parser.add_argument('--rewrite-history', action='store_true', help='with --push: publish a single fresh commit and force-push it')
    parser.add_argument('-m', '--message', default='Update public copy')
    args = parser.parse_args()
    cfg = json.loads(MAP.read_text())
    files, problems = build(cfg)
    print('文件 %d 个 → %s' % (len(files), STAGE.relative_to(ROOT)))
    if problems:
        for rel, kind, value in problems:
            print('泄露风险 %s: %s %s' % (rel, kind, value))
        sys.exit('发现未脱敏内容，已停止；请补充 exports/public-publish.json 后重试')
    print('脱敏检查通过')
    if not args.push:
        return
    if args.rewrite_history:
        git('checkout', '-q', '--orphan', 'fresh')
        git('add', '-A')
        git('commit', '-q', '-m', args.message + '\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
        git('branch', '-M', 'main')
        git('push', '-q', '--force', '-u', 'origin', 'main')
        git('reflog', 'expire', '--expire=now', '--all')
        git('gc', '-q', '--prune=now')
        print('已用单个新提交替换公开历史 https://github.com/%s' % cfg['repo'])
        return
    git('add', '-A')
    if not git('status', '--porcelain', capture=True).strip():
        print('无变化，未提交')
        return
    git('commit', '-q', '-m', args.message + '\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
    git('push', '-q', '-u', 'origin', 'main')
    print('已推送 https://github.com/%s' % cfg['repo'])


if __name__ == '__main__':
    main()
