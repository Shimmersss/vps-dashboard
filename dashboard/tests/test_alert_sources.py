import json
import time
import unittest
from unittest.mock import patch

import helpers
from panel.reachability import Reachability, classify, public_target
import test_xray_dashboard as fixtures


class MainlandTest(unittest.TestCase):
    def setUp(self):
        self.root = helpers.make_env(extra={'reachability': {'enabled': True}})
        self.app, self.ctx = helpers.make_app(self.root)
        self.monitor = self.ctx.reachability

    def tearDown(self):
        helpers.cleanup(self.root)

    def row(self, ok):
        return {'key': 'entry:unicom', 'target': 'entry', 'label': 'Test entry', 'isp': '联通', 'ok': ok, 'error': '' if ok else '连接超时'}

    def test_failure_latch_unknown_and_real_recovery_survive_restart(self):
        self.monitor.commit([self.row(False)])
        self.assertFalse(self.monitor.snapshot()['rows'][0]['alarm'])
        self.monitor.commit([self.row(False)])
        self.assertTrue(self.monitor.snapshot()['rows'][0]['alarm'])
        found = {}; self.ctx.alerts._mainland(found); self.ctx.alerts._sync(found)
        opened = self.ctx.alerts.open_alerts()[0]['id']
        self.ctx.alerts.ack(opened)
        self.monitor.commit([self.row(None)])
        self.assertTrue(self.monitor.snapshot()['rows'][0]['alarm'])
        self.assertEqual(self.monitor.snapshot()['rows'][0]['failures'], 0)
        restarted = Reachability(self.ctx.settings, self.ctx.store, self.ctx.stop)
        self.assertTrue(restarted.snapshot()['rows'][0]['alarm'])
        found = {}; self.ctx.alerts._mainland(found); self.ctx.alerts._sync(found)
        self.assertEqual(self.ctx.alerts.open_alerts()[0]['id'], opened)
        self.monitor.commit([self.row(True)])
        found = {}; self.ctx.alerts._mainland(found); self.ctx.alerts._sync(found)
        self.assertFalse(self.ctx.alerts.open_alerts())
        self.assertTrue(self.ctx.alerts.recent()[0]['resolved'])

    def test_gap_does_not_create_consecutive_failure(self):
        self.monitor.commit([self.row(False)])
        self.monitor.state['at'] -= 5000
        self.monitor.commit([self.row(False)])
        self.assertEqual(self.monitor.snapshot()['rows'][0]['failures'], 1)
        self.assertFalse(self.monitor.snapshot()['rows'][0]['alarm'])

    def test_provider_failure_not_target_failure(self):
        self.monitor.commit([self.row(None)], 'HTTPError')
        self.monitor.commit([self.row(None)], 'HTTPError')
        found = {}; self.ctx.alerts._mainland(found)
        self.assertEqual(set(found), {'reachability:provider'})

    def test_public_target_rejects_secret_paths_and_query(self):
        self.assertEqual(public_target({'host': 'panel.example.test', 'path': '/monitor/login'}), ('panel.example.test', '/monitor/login'))
        for path in ['/subscription/private-token', '/?token=secret', '/monitor/login?token=secret', '//other.test/']:
            with self.assertRaises(ValueError): public_target({'host': 'panel.example.test', 'path': path})
        with self.assertRaises(ValueError): public_target({'host': 'user:secret@panel.example.test'})

    def test_classification_requires_mainland_expected_asn_and_valid_tls(self):
        row = {'probe': {'country': 'CN', 'asn': 4837, 'city': 'Test city'}, 'result': {'status': 'finished', 'statusCode': 200, 'tls': {'authorized': True}}}
        data = {'results': [row]}
        self.assertTrue(classify(data, 4837)['ok'])
        row['result']['tls'] = None
        self.assertFalse(classify(data, 4837)['ok'])
        row['result']['status'] = 'offline'
        self.assertIsNone(classify(data, 4837)['ok'])
        row['probe']['country'] = 'HK'
        self.assertIsNone(classify(data, 4837)['ok'])
        row['probe']['country'] = 'CN'; row['probe']['asn'] = 4134
        self.assertIsNone(classify(data, 4837)['ok'])

    def test_job_restricts_request_and_skips_offline_probe(self):
        self.ctx.settings.values['reachability']['targets'] = [{'id': 'entry', 'label': 'Test', 'host': 'example.test', 'path': '/monitor/login'}]
        calls = []
        def fake_api(path, payload=None):
            calls.append((path, payload))
            if payload: return {'id': 'test-id'}
            return {'status': 'finished', 'results': []}
        with patch('panel.reachability.api', side_effect=fake_api), patch.object(self.ctx.stop, 'wait', return_value=False):
            self.monitor.run()
        posts = [p for _, p in calls if p]
        self.assertEqual(len(posts), 3)
        self.assertTrue(all(p['locations'][0]['country'] == 'CN' for p in posts))
        self.assertTrue(all(r['ok'] is None and r['failures'] == 0 for r in self.monitor.snapshot()['rows']))


class MediaAlertTest(unittest.TestCase):
    setUp = fixtures.DashboardTest.setUp
    tearDown = fixtures.DashboardTest.tearDown
    def test_target_rounds_ack_stale_and_recovery(self):
        self.ctx.settings.values['probes']['enabled'] = True
        # Use the actual structured outbound from the fixture.
        node = self.ctx.xray.rows['main'][0]
        tag = node['outbound']
        def save(at, ok):
            with patch('panel.xray.time.time', return_value=at):
                self.ctx.xray.ledger.save_probes([('main', tag, {'targets': {'Instagram': {'ok': int(ok), 'error': '' if ok else 'HTTP 403'}, 'YouTube': {'ok': 1}}})])
        save(self.now - 300, False)
        found = {}; self.ctx.alerts._xray_media(found); self.assertFalse(found)
        save(self.now, False)
        found = {}; self.ctx.alerts._xray_media(found)
        self.assertEqual(len(found), 1)
        self.assertIn('Instagram', next(iter(found.values()))[2])
        self.ctx.alerts._sync(found)
        self.ctx.alerts.ack(self.ctx.alerts.open_alerts()[0]['id'])
        for _ in range(3):
            found = {}; self.ctx.alerts._xray_media(found); self.ctx.alerts._sync(found)
        self.assertEqual(len(self.ctx.alerts.recent()), 1)
        with patch('panel.alerts.time.time', return_value=self.now + 2000):
            found = {}; self.ctx.alerts._xray_media(found)
        self.assertEqual(len(found), 2)  # unresolved media failure + unknown/stale monitor
        save(self.now + 1, True)
        found = {}; self.ctx.alerts._xray_media(found); self.ctx.alerts._sync(found)
        self.assertFalse(self.ctx.alerts.open_alerts())

    def test_round_gap_breaks_failure_streak(self):
        tag = self.ctx.xray.rows['main'][0]['outbound']
        for at in [self.now - 5000, self.now]:
            with patch('panel.xray.time.time', return_value=at):
                self.ctx.xray.ledger.save_probes([('main', tag, {'targets': {'Instagram': {'ok': 0}}})])
        self.assertEqual(self.ctx.xray.ledger.probe_health(2, 300)['main:' + tag]['Instagram']['failures'], 1)


if __name__ == '__main__':
    unittest.main()
