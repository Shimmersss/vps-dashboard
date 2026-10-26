import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import helpers
from panel import xray, xray_dashboard
from test_xray import config


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xray-dashboard-')
        path = Path(self.temp.name) / 'core.json'
        path.write_text(json.dumps(config()))
        instance = {'id': 'main', 'label': 'Xray test', 'service': 'test.service', 'config_path': str(path), 'api_address': '127.0.0.1:12345', 'external_tls': True, 'external_port': 443}
        self.root = helpers.make_env(extra={'xray': {'instances': [instance]}})
        self.app, self.ctx = helpers.make_app(self.root)
        self.client = self.app.test_client()
        self.now = int(time.time())
        nodes, _ = xray.topology(instance)
        self.ctx.xray.ledger.ingest('main', 'g', {'in-a': (0, 0)}, self.now - 4, self.ctx.store.day())
        traffic = self.ctx.xray.ledger.ingest('main', 'g', {'in-a': (123, 456)}, self.now - 2, self.ctx.store.day())['in-a']
        node = {k: v for k, v in nodes[0].items() if not k.startswith('_')} | traffic | {'online_sources': ['198.51.100.7']}
        self.ctx.xray.rows['main'] = [node]
        self.ctx.xray.states['main'] = {'id': 'main', 'label': 'Xray test', 'connected': True, 'last_success': self.now, 'error': ''}
        self.ctx.store.ingest(helpers.batch(helpers.event(helpers.pb.EVENT_NEW, 'legacy', helpers.conn(up=999999, down=888888))))
        self.csrf, _ = helpers.login(self.client)

    def tearDown(self):
        self.ctx.xray.ledger.db.close()
        helpers.cleanup(self.root); self.temp.cleanup()

    def test_primary_and_totals_are_not_borrowed_from_legacy(self):
        overview = self.client.get('/monitor/api/overview').get_json()
        x = self.client.get('/monitor/api/xray').get_json()
        self.assertEqual(overview['primary_engine'], 'xray')
        self.assertEqual(overview['totals']['upload'], 999999)
        self.assertEqual((x['totals']['upload'], x['totals']['download']), (123, 456))
        self.assertNotIn('connections', x['totals'])
        self.assertFalse(x['capabilities']['client_bytes'])
        self.assertEqual(x['sources'][0]['ip'], '198.51.100.7')
        self.assertNotIn('upload', x['sources'][0])

    def test_real_persistent_details_and_period_aggregation(self):
        for span, step in [('24h', 60), ('7d', 3600), ('30d', 3600)]:
            x = self.client.get('/monitor/api/xray/details?range=' + span).get_json()
            self.assertEqual(x['engine'], 'xray')
            self.assertEqual(x['sample_step'], step)
            self.assertEqual(sum(r['upload'] for r in x['minutes']), 123)
            self.assertEqual(sum(r['download'] for r in x['daily']), 456)
            self.assertNotIn('domain', json.dumps(x))

    def test_failed_echo_never_becomes_a_measured_exit(self):
        self.ctx.xray.ledger.save_probes([('main', 'out-a', {'targets': {'trace': {'ok': 0, 'exit_ip': '203.0.113.7'}, 'YouTube': {'ok': 1, 'exit_ip': ''}}})])
        x = self.client.get('/monitor/api/xray').get_json()
        self.assertEqual(x['outbounds'][0]['exit_ip'], '')
        self.assertIsNone(x['outbounds'][0]['geo'])
        self.assertEqual(len(self.client.get('/monitor/api/xray/details').get_json()['probe_history']), 1)

    def test_labels_are_scoped_and_persist_without_core_edits(self):
        headers = {'Origin': helpers.ORIGIN, 'X-CSRF-Token': self.csrf}
        before = Path(self.ctx.xray.instances[0]['config_path']).read_bytes()
        r = self.client.put('/monitor/api/prefs', json={'labels.xray': {'main:in-a': '新的面板名称'}}, headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get('/monitor/api/xray').get_json()['nodes'][0]['label'], '新的面板名称')
        self.assertEqual(Path(self.ctx.xray.instances[0]['config_path']).read_bytes(), before)
        bad = self.client.put('/monitor/api/prefs', json={'labels.xray': {'x': 'x' * 41}}, headers=headers)
        self.assertEqual(bad.status_code, 400)

    def test_login_and_csrf_cover_the_new_endpoints(self):
        anonymous = self.app.test_client()
        for path in ['xray', 'xray/details', 'xray/network']:
            self.assertEqual(anonymous.get('/monitor/api/' + path).status_code, 401)
        self.assertEqual(self.client.post('/monitor/api/xray/probes/run', json={}).status_code, 403)

    def test_alert_scope_excludes_unused_legacy_core(self):
        self.ctx.alerts._sync({'collector': ('collector', 'critical', 'legacy failed'), 'xray:main': ('collector', 'critical', 'xray failed'), 'cert': ('cert', 'warning', 'certificate'), 'probe:direct': ('probe', 'warning', 'legacy probe')})
        rows = self.client.get('/monitor/api/alerts?engine=xray').get_json()['open']
        self.assertEqual({r['key'] for r in rows}, {'xray:main', 'cert'})

    def test_missing_or_stale_online_data_is_not_zero_clients(self):
        self.ctx.xray.rows['main'][0]['online_sources'] = None
        x = xray_dashboard.present(self.ctx)
        self.assertEqual(x['source_nodes_supported'], 0)
        self.assertEqual(x['sources'], [])
        self.ctx.xray.states['main']['last_success'] = self.now - 30
        x = xray_dashboard.present(self.ctx)
        self.assertIsNone(x['nodes'][0]['down_rate'])
        self.assertFalse(x['instances'][0]['connected'])


if __name__ == '__main__':
    unittest.main()
