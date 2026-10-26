"""Condition-based alerts: opened once per key, resolved automatically when the condition clears.

Optional webhook (secrets: alert_webhook_url) receives a JSON POST for newly opened alerts.
"""
import json
import logging
import threading
import time
import urllib.request

from . import system

log = logging.getLogger(__name__)


class Alerts:
    def __init__(self, ctx):
        self.ctx = ctx
        self.webhook = ctx.settings.secrets.get('alert_webhook_url', '')

    def open_alerts(self):
        rows = self.ctx.store.reader().execute('SELECT * FROM alerts WHERE resolved=0 ORDER BY opened DESC').fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit=100):
        rows = self.ctx.store.reader().execute('SELECT * FROM alerts ORDER BY opened DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    def ack(self, alert_id=None):
        with self.ctx.store.write() as c:
            if alert_id:
                c.execute('UPDATE alerts SET acked=1 WHERE id=?', (int(alert_id),))
            else:
                c.execute('UPDATE alerts SET acked=1 WHERE resolved=0')

    def evaluate(self):
        found = {}
        ctx, s = self.ctx, self.ctx.settings
        health = ctx.health
        down_for = time.time() - (health.get('last_event', 0) / 1000 or health['started'])
        if not health.get('connected') and down_for >= int(s.get('alerts.collector_down_seconds')):
            found['collector'] = ('collector', 'critical', '内核事件采集中断 %d 秒：%s' % (down_for, health.get('last_error') or '未知原因'))
        limit = int(s.get('alerts.probe_failures'))
        if s.get('probes.enabled') and limit > 0:
            for outbound in ctx.topology.describe()['outbounds']:
                if outbound['probe'] and ctx.prober.failures(outbound['tag']) >= limit:
                    found['probe:' + outbound['tag']] = ('probe', 'warning', '出口 %s 连续 %d 轮探测失败' % (outbound['label'], limit))
                changes = ctx.prober.exit_changes(outbound['tag'], 2)
                if len(changes) == 2 and time.time() - changes[-1]['at'] < 86400:
                    found['exit:%s:%s' % (outbound['tag'], changes[-1]['exit_ip'])] = (
                        'exit', 'info', '出口 %s 的公网地址已变化：%s → %s' % (outbound['label'], changes[0]['exit_ip'], changes[1]['exit_ip']))
        cert = system.certificate(ctx.cert_path())
        days = int(s.get('alerts.cert_days'))
        if cert.get('days_left') is not None and cert['days_left'] < days:
            found['cert'] = ('cert', 'warning', 'TLS 证书 %.1f 天后到期' % cert['days_left'])
        usage = system.disk(str(s.data_dir))
        if usage['percent'] >= float(s.get('alerts.disk_percent')):
            found['disk'] = ('disk', 'warning', '数据盘已用 %.1f%%' % usage['percent'])
        for name, state in system.services(list(s.get('alerts.services') or [])).items():
            if state != 'active':
                found['service:' + name] = ('service', 'critical', '服务 %s 状态 %s' % (name, state))
        threshold = int(s.get('alerts.ip_daily_bytes') or 0)
        if threshold > 0:
            day = ctx.store.day()
            for r in ctx.store.reader().execute('SELECT ip,upload+download b FROM daily WHERE day=? AND upload+download>=?', (day, threshold)):
                found['quota:%s:%s' % (day, r['ip'])] = ('quota', 'warning', '%s 今日流量超过阈值' % r['ip'])
        self._sync(found)

    def _sync(self, found):
        now = int(time.time())
        opened = []
        with self.ctx.store.write() as c:
            current = {r['key']: r['id'] for r in c.execute('SELECT id,key FROM alerts WHERE resolved=0')}
            for key, (kind, level, message) in found.items():
                if key in current:
                    c.execute('UPDATE alerts SET message=? WHERE id=?', (message, current[key]))
                else:
                    c.execute('INSERT INTO alerts(key,kind,level,message,opened) VALUES(?,?,?,?,?)', (key, kind, level, message, now))
                    opened.append({'kind': kind, 'level': level, 'message': message})
            for key, alert_id in current.items():
                if key not in found:
                    c.execute('UPDATE alerts SET resolved=? WHERE id=?', (now, alert_id))
        if opened and self.webhook:
            threading.Thread(target=self._notify, args=(opened,), daemon=True).start()

    def _notify(self, items):
        title = self.ctx.settings.get('site.title')
        for item in items:
            body = json.dumps({'title': title, 'level': item['level'], 'kind': item['kind'], 'message': item['message'],
                               'text': '[%s] %s' % (title, item['message'])}).encode()
            try:
                request = urllib.request.Request(self.webhook, data=body, headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(request, timeout=10).read(256)
            except Exception as exc:
                log.warning('Alert webhook failed: %s', type(exc).__name__)
