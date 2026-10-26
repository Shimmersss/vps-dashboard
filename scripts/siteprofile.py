"""Dashboard deployment profile (dashboard/deploy/site.json) shared by maintenance scripts.

The public URL and paths come from the profile; the server address is only the SSH target in .env.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'dashboard/deploy/site.json'


def load():
    if not PROFILE.exists():
        raise SystemExit('缺少 dashboard/deploy/site.json；请从 site.example.json 复制并填写')
    profile = json.loads(PROFILE.read_text())
    parts = urlsplit(profile['public_url'])
    profile['origin'] = parts.scheme + '://' + parts.netloc
    profile['host'] = parts.hostname
    profile['base_path'] = '/' + profile.get('base_path', '/monitor').strip('/')
    profile['panel_url'] = profile['origin'] + profile['base_path'] + '/'
    return profile


def ssh_host():
    """Current server address, from the project .env (the only place it is configured)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('runner', ROOT / 'singbox-exit-ops/scripts/ssh_run.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner.load_settings(ROOT / '.env')['SSH_HOST']
