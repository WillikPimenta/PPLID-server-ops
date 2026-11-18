import importlib.util
import io
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('independent_probe', Path(__file__).resolve().parents[1] / 'tools' / 'independent_availability_probe.py')
external = importlib.util.module_from_spec(spec)
spec.loader.exec_module(external)


class ExternalProbeTests(unittest.TestCase):
    def test_readiness_success_and_invalid_html(self):
        for body, successful in [(b'{"status":"healthy"}', True), (b'{"status":"unhealthy"}', False), (b'<html>Login</html>', False)]:
            response = io.BytesIO(body)
            response.status = 200
            with patch.object(external.urllib.request, 'urlopen', return_value=response):
                result = external.probe('https://example.test/ready/', 3)
            self.assertEqual(result['successful'], successful)
            self.assertEqual(result['source'], 'independent')

    def test_timeout_and_http_failure_are_preserved_without_sensitive_details(self):
        for error in [TimeoutError('secret token'), urllib.error.HTTPError('https://secret/', 503, 'secret', {}, io.BytesIO(b'secret'))]:
            with patch.object(external.urllib.request, 'urlopen', side_effect=error):
                result = external.probe('https://example.test/ready/', 3)
            self.assertFalse(result['successful'])
            self.assertNotIn('secret', str(result))

    def test_unsafe_or_invalid_arguments_are_rejected_before_any_request(self):
        with patch.object(external.urllib.request, 'urlopen') as request, patch('sys.stderr', new_callable=io.StringIO):
            for url in ['file:///tmp/data', 'https://user:password@example.test/', 'https://example.test/?token=secret']:
                with self.assertRaises(SystemExit):
                    external.main(['--url', url, '--output-dir', 'unused', '--once'])
            with self.assertRaises(SystemExit):
                external.main(['--url', 'https://example.test/', '--output-dir', 'unused', '--interval', '0'])
        request.assert_not_called()
