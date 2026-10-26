"""Xray-first presentation. Never borrow sing-box bytes or connection counts."""
import time


GEO_KEYS = ('location', 'isp', 'asn', 'lat', 'lon', 'country_code', 'precision', 'source')


def present(ctx):
    data = ctx.xray.snapshot(detail=False)
    sources = {}
    outbounds = {}
    supported = 0
    for node in data['nodes']:
        node['outbound_key'] = node['instance'] + ':' + node['outbound'] if node['outbound'] else ''
        if node['online_sources'] is not None:
            supported += 1
            for ip in node['online_sources']:
                entry = sources.setdefault(ip, {'ip': ip, 'nodes': [], 'sampled_at': node['sampled_at']})
                entry['nodes'].append(node['key'])
                entry['sampled_at'] = max(entry['sampled_at'], node['sampled_at'])
        if node['outbound_key']:
            out = outbounds.setdefault(node['outbound_key'], {
                'key': node['outbound_key'], 'instance': node['instance'], 'tag': node['outbound'],
                'label': node['label'] + ' · 出口', 'nodes': [], 'probe': node['probe'],
                'upload': 0, 'download': 0, 'up_rate': 0, 'down_rate': 0, 'fresh': True,
            })
            out['nodes'].append(node['key'])
            for key in ('upload', 'download'):
                out[key] += node[key]
            for key in ('up_rate', 'down_rate'):
                if node[key] is None or out[key] is None:
                    out[key] = None
                else:
                    out[key] += node[key]
            out['fresh'] = out['fresh'] and node['fresh']
    for entry in sources.values():
        geo = ctx.geo.lookup(entry['ip'])
        entry['geo'] = {k: geo[k] for k in GEO_KEYS}
    for out in outbounds.values():
        result = data['probes'].get(out['key'])
        trace = result.get('targets', {}).get('trace', {}) if result else {}
        # Only a validated echo is an observed exit address.
        out['exit_ip'] = trace.get('exit_ip', '') if trace.get('ok') else ''
        out['probe_at'] = result['at'] if result else None
        out['targets'] = result.get('targets', {}) if result else {}
        out['geo'] = {k: ctx.geo.lookup(out['exit_ip'])[k] for k in GEO_KEYS} if out['exit_ip'] else None
    data.update(engine='xray', sources=list(sources.values()), source_nodes_supported=supported,
                outbounds=list(outbounds.values()), since=min((n['since'] for n in data['nodes']), default=None),
                probe_interval=ctx.settings.get('probes.interval_seconds'),
                probe_targets=[t['name'] for t in ctx.settings.get('probes.targets') or [] if t['name'] != 'trace'], probes_enabled=bool(ctx.settings.get('probes.enabled')),
                capabilities={'node_bytes': True, 'node_rates': True, 'daily_bytes': True, 'connection_count': False,
                              'per_connection_bytes': False, 'client_bytes': False, 'destination_history': False,
                              'close_connection': False, 'access_control': False})
    return data


def details(ctx, span):
    seconds = {'24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400}.get(span, 86400)
    now = int(time.time())
    step = 60 if seconds <= 86400 else 3600
    result = ctx.xray.ledger.details(now - seconds, ctx.store.day(now - seconds), step) if ctx.xray.ledger else {
        'daily': [], 'minutes': [], 'sample_step': step, 'probe_history': [], 'probe_history_truncated': False}
    result.update(engine='xray', range=span, since=now - seconds,
                  history_scope='每分钟按实例记录流量；逐连接、目标域名和客户端字节未由 Xray 提供。探测历史从本版开始保存，最多保留 7 天。')
    return result


def alerts_for_xray(ctx, rows):
    services = {i['service'] for i in ctx.xray.instances}
    services.update(n for n in (ctx.settings.get('alerts.services') or []) if 'nginx' in n)
    return [r for r in rows if r['key'].startswith(('xray:', 'reachability:')) or r['key'] in ('cert', 'disk')
            or r['key'] in {'service:' + s for s in services}]
