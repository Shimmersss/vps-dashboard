"""Public mainland HTTPS vantage points. Never submit subscription tokens or bodies."""
import copy
import json
import re
import threading
import time
import urllib.error
import urllib.request

API = 'https://api.globalping.io/v1'
ISPS = {'telecom': ('电信', 4134), 'unicom': ('联通', 4837), 'mobile': ('移动', 9808)}
# Only explicitly safe public resources may be sent to a public measurement archive.
SAFE_PATHS = {'/', '/monitor/login', '/favicon.ico'}


def public_target(target):
    host = target.get('host', '')
    path = target.get('path', '/')
    if not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host) or '.' not in host:
        raise ValueError('Public DNS hostname required')
    if path not in SAFE_PATHS:
        raise ValueError('Only a token-free public health path may be shared')
    return host, path


def api(path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(API + path, body, {'Content-Type': 'application/json', 'User-Agent': 'vps-dashboard/2.3.1'})
    # No inherited proxy, cookies, credentials, or arbitrary API destination.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Measurement response too large')
    return json.loads(raw)


def classify(data, asn):
    matches = [r for r in data.get('results', []) if r.get('probe', {}).get('country') == 'CN'
               and str(r.get('probe', {}).get('asn')) == str(asn)]
    if not matches:
        return {'ok': None, 'error': '该运营商大陆节点暂不可用'}
    row = matches[0]
    result, probe = row.get('result', {}), row['probe']
    status = result.get('status')
    if status not in ('finished', 'failed'):
        return {'ok': None, 'error': '公开探测节点离线或未完成'}
    code = result.get('statusCode')
    # A received 403/404 proves transport reachability but not a working endpoint.
    # TLS must be trusted and the exact safe endpoint must return 2xx.
    ok = status == 'finished' and isinstance(code, int) and 200 <= code < 300 and (result.get('tls') or {}).get('authorized') is True
    reason = 'DNS、连接、TLS 或请求失败'
    raw = str(result.get('rawOutput', '')).lower()
    if isinstance(code, int):
        reason = 'HTTP %d' % code
        if 200 <= code < 300 and (result.get('tls') or {}).get('authorized') is not True:
            reason = 'TLS 证书未通过校验'
    elif 'resolve' in raw:
        reason = 'DNS 解析失败'
    elif 'timed out' in raw or 'timeout' in raw:
        reason = '连接或请求超时'
    elif 'certificate' in raw or 'ssl' in raw:
        reason = 'TLS 连接或证书校验失败'
    elif 'connect' in raw:
        reason = '连接失败'
    return {'ok': ok, 'http_status': code, 'city': str(probe.get('city', ''))[:60], 'asn': asn,
            'total_ms': result.get('timings', {}).get('total'),
            'error': '' if ok else reason}


class Reachability:
    def __init__(self, settings, store, stop):
        self.settings, self.store, self.stop = settings, store, stop
        self.lock = threading.RLock()
        self.running = False
        try:
            self.state = json.loads(store.meta('mainland_https', '{}'))
        except (ValueError, TypeError):
            self.state = {}

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            result.update(enabled=bool(self.settings.get('reachability.enabled')), running=self.running,
                          interval=self.settings.get('reachability.interval_seconds'),
                          failures=self.settings.get('reachability.failures'),
                          scope='公开大陆节点检测入口 HTTPS；不提交订阅令牌，不代表完整订阅下载或 VMess 握手。',
                          provider='Globalping', subscription_configured=False)
            return result

    def start_if_due(self):
        with self.lock:
            interval = max(600, int(self.settings.get('reachability.interval_seconds')))
            if not self.settings.get('reachability.enabled') or not self.settings.get('reachability.targets') or self.running or time.time() - self.state.get('at', 0) < interval:
                return False
            self.running = True
        threading.Thread(target=self.run, name='mainland-https', daemon=True).start()
        return True

    def commit(self, rows, provider_error=''):
        now = int(time.time())
        provider_error = provider_error or ('大陆运营商节点缺失、离线或未完成' if any(r['ok'] is None for r in rows) else '')
        with self.lock:
            old = self.state
            previous = {r['key']: r for r in old.get('rows', [])}
            threshold = max(1, int(self.settings.get('reachability.failures')))
            interval = max(600, int(self.settings.get('reachability.interval_seconds')))
            for row in rows:
                before = previous.get(row['key'], {})
                contiguous = now - old.get('at', 0) <= interval * 3
                row['failures'] = (before.get('failures', 0) if contiguous else 0) + 1 if row['ok'] is False else 0
                row['alarm'] = (row['failures'] >= threshold or before.get('alarm', False)) if row['ok'] is not True else False
                row['at'] = now
            errors = old.get('provider_failures', 0) + 1 if provider_error else 0
            self.state = {'at': now, 'rows': rows, 'provider_error': provider_error, 'provider_failures': errors}
            with self.store.write() as c:
                c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('mainland_https', json.dumps(self.state)))

    def run(self):
        rows, provider_error = [], ''
        try:
            for target in self.settings.get('reachability.targets') or []:
                host, path = public_target(target)
                jobs = {}
                for isp, (label, asn) in ISPS.items():
                    key = target['id'] + ':' + isp
                    try:
                        created = api('/measurements', {'type': 'http', 'target': host, 'locations': [{'country': 'CN', 'asn': asn, 'limit': 1}],
                            'measurementOptions': {'protocol': 'HTTPS', 'ipVersion': 4, 'request': {'method': 'GET', 'path': path}}})
                        mid = created.get('id', '')
                        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', mid):
                            raise ValueError('Invalid measurement ID')
                        jobs[key] = (isp, label, asn, mid)
                    except Exception as exc:
                        provider_error = type(exc).__name__
                        rows.append({'key': key, 'target': target['id'], 'label': target['label'], 'isp': label, 'ok': None, 'error': '公开检测服务未接受任务'})
                for key, (isp, label, asn, mid) in jobs.items():
                    result = {'ok': None, 'error': '公开检测任务未完成'}
                    for _ in range(16):
                        if self.stop.wait(2):
                            return
                        data = api('/measurements/' + mid)
                        if data.get('status') != 'in-progress':
                            result = classify(data, asn)
                            break
                    rows.append({'key': key, 'target': target['id'], 'label': target['label'], 'isp': label, **result})
            self.commit(rows, provider_error)
        except Exception as exc:
            # Preserve previous outage latches while the provider itself is unknown.
            self.commit(self.snapshot().get('rows', []) and [dict(r, ok=None, error='检测服务异常，状态未知') for r in self.snapshot()['rows']] or rows,
                        type(exc).__name__)
        finally:
            with self.lock:
                self.running = False
