import unittest
from unittest.mock import patch
from panel import probes, settings


class MediaProbeTests(unittest.TestCase):
    def test_media_http_statuses_do_not_invent_exit_ip(self):
        for status, ok in [(200, 1), (204, 1), (302, 1), (403, 0), (429, 0), (500, 0)]:
            with self.subTest(status=status), patch.object(probes, 'fetch', return_value=(status, 'ip=203.0.113.1', 2, 3)):
                r = probes.probe({'name': 'media', 'url': 'https://example.com', 'kind': 'http'})
                self.assertEqual(r['ok'], ok)
                self.assertEqual(r['exit_ip'], '')

    def test_trace_still_requires_valid_echo(self):
        for body, ok in [('hello', 0), ('ip=invalid', 0), ('ip=203.0.113.1', 1)]:
            with patch.object(probes, 'fetch', return_value=(200, body, 2, 3)):
                self.assertEqual(probes.probe({'name': 'trace', 'url': 'https://example.com'})['ok'], ok)

    def test_defaults_use_media_and_keep_trace(self):
        targets = settings.DEFAULTS['probes']['targets']
        self.assertEqual([t['name'] for t in targets], ['trace', 'Instagram', 'YouTube', 'Facebook'])

    def test_auth_response_must_match_status_and_body(self):
        target = {'name': 'api', 'url': 'https://example.com/models', 'kind': 'http',
                  'expected_status': 401, 'expected_body': '"error"'}
        for status, body, ok in [(401, '{"error":"unauthorized"}', 1), (401, 'blocked', 0),
                                 (403, '{"error":"denied"}', 0)]:
            with self.subTest(status=status, body=body):
                with patch.object(probes, 'fetch', return_value=(status, body, 2, 3)):
                    self.assertEqual(probes.probe(target)['ok'], ok)

    def test_singbox_outbound_override_and_default(self):
        default = [{'name': 'trace'}]
        ai_targets = [{'name': 'trace'}, {'name': 'AI API'}]
        cfg = {'probes.targets': default, 'probes.targets_by_outbound': {'ai': ai_targets, 'skip': []}}
        self.assertEqual(probes.targets_for(cfg, 'ai'), ai_targets)
        self.assertEqual(probes.targets_for(cfg, 'direct'), default)
        self.assertEqual(probes.targets_for(cfg, 'skip'), [])


if __name__ == '__main__':
    unittest.main()
