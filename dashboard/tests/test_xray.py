import concurrent.futures
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

import helpers
import grpc
from panel import xray, xray_proto as pb


def config():
    return {'inbounds': [{'tag': 'in-a', 'protocol': 'vmess', 'listen': '127.0.0.1', 'port': 12001,
                          'settings': {'clients': [{'id': '00000000-0000-4000-8000-000000000001', 'email': 'test@example.invalid'}]},
                          'streamSettings': {'network': 'ws'}}],
            'outbounds': [{'tag': 'out-a', 'protocol': 'socks', 'settings': {'servers': [{'address': 'proxy.example.invalid', 'port': 1080, 'users': [{'user': 'fixture-user', 'pass': 'fixture-secret'}]}]}}],
            'routing': {'rules': [{'type': 'field', 'inboundTag': ['in-a'], 'outboundTag': 'out-a'}]}}


class XrayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xray-unit-')
        self.path = Path(self.tmp.name) / 'xray.json'
        self.path.write_text(json.dumps(config()))
        self.instance = {'id': 'main', 'label': 'test', 'service': 'test.service', 'config_path': str(self.path), 'api_address': '127.0.0.1:8311', 'external_tls': True, 'external_port': 443}
        self.root = helpers.make_env(extra={'xray': {'instances': [self.instance]}})
        self.settings = helpers.settings_mod.load(self.root / 'settings.json', self.root / 'config.json', environ={})

    def tearDown(self):
        helpers.cleanup(self.root)
        self.tmp.cleanup()

    def test_loopback_and_instance_validation(self):
        for patch in [{'api_address': '0.0.0.0:8311'}, {'api_address': 'api.example.com:8311'}, {'service': 'x;true'}, {'id': '../unsafe'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                xray.validate_instance(self.instance | patch)

    def test_topology_private_credentials_and_exact_route(self):
        nodes, credentials = xray.topology(self.instance)
        self.assertEqual(nodes[0]['outbound'], 'out-a')
        self.assertEqual(nodes[0]['port'], 443)
        self.assertTrue(nodes[0]['tls'])
        self.assertEqual(credentials['out-a']['password'], 'fixture-secret')
        public = {k: v for k, v in nodes[0].items() if not k.startswith('_')}
        self.assertNotIn('fixture-secret', json.dumps(public))
        self.assertNotIn('test@example.invalid', json.dumps(public))
        c = config()
        c['routing']['rules'].insert(0, {'type': 'field', 'inboundTag': ['in-a'], 'domain': ['example.com'], 'outboundTag': 'another'})
        self.path.write_text(json.dumps(c))
        self.assertFalse(xray.topology(self.instance)[0][0]['probe'])

    def test_ledger_replay_restart_rollover_and_gap(self):
        ledger = xray.Ledger(self.settings)
        def sample(gen, up, down, now, day='2026-01-01'):
            return ledger.ingest('main', gen, {'in-a': (up, down)}, now, day)['in-a']
        self.assertEqual(sample('g1', 100, 200, 100)['upload'], 0)
        self.assertEqual(sample('g1', 160, 300, 102)['up_rate'], 30)
        self.assertEqual(sample('g1', 160, 300, 104)['upload'], 60)
        reopened = xray.Ledger(self.settings)
        self.assertEqual(reopened.ingest('main', 'g1', {'in-a': (170, 330)}, 106, '2026-01-01')['in-a']['upload'], 70)
        r = sample('g2', 5, 8, 108, '2026-01-02')
        self.assertEqual((r['upload'], r['today_upload']), (75, 5))
        self.assertIsNone(r['up_rate'])
        self.assertTrue(r['counter_reset'])
        self.assertIsNone(sample('g2', 100, 200, 140)['up_rate'])
        self.assertEqual(sum(r['upload'] for r in ledger.series(140)), 170)
        one_reset = sample('g2', 110, 10, 142)
        self.assertEqual(one_reset['upload'], 180, 'resetting one direction must not recount the other')
        self.assertEqual((self.settings.data_dir / 'xray-traffic.sqlite3').stat().st_mode & 0o777, 0o600)
        reopened.db.close(); ledger.db.close()

    def test_failure_missing_statistics_never_reports_zero_success(self):
        class Fake:
            def __init__(self, instance): pass
            def counters(self): return {}
        m = xray.Monitor(self.settings, lambda t: '2026-01-01', threading.Event(), Fake, lambda i: 'g')
        m.poll()
        state = m.snapshot()
        self.assertFalse(state['instances'][0]['connected'])
        self.assertEqual(state['nodes'], [])
        m.ledger.db.close()

    def test_poll_hides_stale_rates_and_credentials(self):
        class Fake:
            def __init__(self, instance): self.fail = False
            def counters(self):
                if self.fail: raise RuntimeError('secret must not be returned')
                return {'inbound>>>in-a>>>traffic>>>uplink': 100, 'inbound>>>in-a>>>traffic>>>downlink': 200}
            def online(self, email): return None
        m = xray.Monitor(self.settings, lambda t: '2026-01-01', threading.Event(), Fake, lambda i: 'g')
        m.poll(); first = m.snapshot()
        self.assertTrue(first['instances'][0]['connected'])
        self.assertIsNone(first['nodes'][0]['online_sources'])
        m.clients['main'].fail = True
        m.poll(); last = m.snapshot()
        self.assertIsNone(last['nodes'][0]['up_rate'])
        self.assertFalse(last['nodes'][0]['fresh'])
        for secret in ['fixture-secret', 'test@example.invalid', 'secret must not be returned']:
            self.assertNotIn(secret, json.dumps(last))
        m.ledger.db.close()

    def test_routes_require_login_and_csrf(self):
        app, ctx = helpers.make_app(self.root)
        client = app.test_client()
        self.assertEqual(client.get('/monitor/api/xray').status_code, 401)
        csrf, _ = helpers.login(client)
        self.assertEqual(client.get('/monitor/api/xray').status_code, 200)
        self.assertEqual(client.post('/monitor/api/xray/probes/run', json={}).status_code, 403)


class WireTest(unittest.TestCase):
    def test_real_grpc_wire_and_read_only_requests(self):
        seen = []
        def query(req, context):
            seen.append((req.pattern, req.reset))
            response = pb.QueryStatsResponse()
            response.stat.add(name='inbound>>>local>>>traffic>>>uplink', value=123)
            return response
        def online(req, context):
            seen.append((req.name, req.reset))
            response = pb.GetStatsOnlineIpListResponse(name=req.name)
            response.ips['198.51.100.7'] = 123
            return response
        server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=2))
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler('xray.app.stats.command.StatsService', {
            'QueryStats': grpc.unary_unary_rpc_method_handler(query, request_deserializer=pb.QueryStatsRequest.FromString, response_serializer=pb.QueryStatsResponse.SerializeToString),
            'GetStatsOnlineIpList': grpc.unary_unary_rpc_method_handler(online, request_deserializer=pb.GetStatsRequest.FromString, response_serializer=pb.GetStatsOnlineIpListResponse.SerializeToString),
        }),))
        port = server.add_insecure_port('127.0.0.1:0'); server.start()
        try:
            c = xray.Client({'id': 'wire', 'api_address': '127.0.0.1:' + str(port), 'service': 'test.service'})
            self.assertEqual(c.counters()['inbound>>>local>>>traffic>>>uplink'], 123)
            self.assertEqual(c.online('test@example.invalid'), {'198.51.100.7': 123})
            self.assertTrue(all(not reset for _, reset in seen))
            c.channel.close()
        finally:
            server.stop(0).wait()


class NativeCoreTest(unittest.TestCase):
    def test_local_native_core_multi_config_and_real_traffic(self):
        binary = Path(os.environ.get('XRAY_TEST_BINARY', str(Path.home() / 'Applications/ServerProxyManager.app/Contents/Resources/xray')))
        self.assertTrue(binary.is_file(), 'Native Xray runtime is required')
        def free_port():
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0)); return s.getsockname()[1]
        api, inbound = free_port(), free_port()
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'xray-real-local-traffic-' * 1000
                self.send_response(200);self.send_header('Content-Length', str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        with tempfile.TemporaryDirectory(prefix='xray-native-') as folder:
            base, overlay = Path(folder) / 'base.json', Path(folder) / 'stats.json'
            base.write_text(json.dumps({'log': {'loglevel': 'none'}, 'inbounds': [{'tag': 'local', 'protocol': 'socks', 'listen': '127.0.0.1', 'port': inbound, 'settings': {'auth': 'noauth'}}], 'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}], 'routing': {'rules': [{'type': 'field', 'inboundTag': ['local'], 'outboundTag': 'direct'}]}}))
            overlay.write_text(json.dumps({'stats': {}, 'api': {'tag': 'monitor-api', 'listen': '127.0.0.1:' + str(api), 'services': ['StatsService']}, 'policy': {'system': {'statsInboundUplink': True, 'statsInboundDownlink': True}}}))
            before = base.read_bytes()
            command = [str(binary), 'run', '-config', str(base), '-config', str(overlay)]
            check = subprocess.run(command + ['-test'], capture_output=True, timeout=15)
            self.assertEqual(check.returncode, 0)
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            c = xray.Client({'id': 'native', 'api_address': '127.0.0.1:' + str(api), 'service': 'test.service'})
            try:
                for _ in range(50):
                    self.assertIsNone(proc.poll())
                    try: initial = c.counters();break
                    except grpc.RpcError: time.sleep(.1)
                else: self.fail('Native API unavailable')
                r = subprocess.run(['/usr/bin/curl', '-q', '--proxy', 'socks5h://127.0.0.1:' + str(inbound), '--noproxy', '', '--max-time', '8', '-fsS', 'http://127.0.0.1:' + str(server.server_port)], capture_output=True, timeout=10)
                self.assertEqual(r.returncode, 0)
                self.assertGreater(len(r.stdout), 20000)
                counters = c.counters()
                self.assertGreater(counters['inbound>>>local>>>traffic>>>downlink'], initial['inbound>>>local>>>traffic>>>downlink'])
                self.assertEqual(base.read_bytes(), before)
                self.assertEqual(c.counters(), counters, 'QueryStats must never reset counters')
            finally:
                c.channel.close();proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=5)
        server.shutdown();server.server_close();thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
