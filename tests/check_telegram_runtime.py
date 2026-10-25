"""Run explicitly against the isolated runtime stack, never production."""
import json
import time
import unittest
import urllib.error
import urllib.request

BASE = 'http://backend:8000'


def request(method, path, data=None, headers=None):
    headers = dict(headers or {})
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, method=method, headers=headers,
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, json.load(response)


class RuntimeTests(unittest.TestCase):
    def test_backend_health(self):
        self.assertEqual(request('GET', '/health'), (200, {'status': 'ok'}))

    def test_scoped_token_cannot_claim_worker_job(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request('POST', '/api/worker/jobs/next', headers={
                'X-Worker-Token': 'telegram-test-token-with-at-least-32-characters'})
        self.assertEqual(ctx.exception.code, 401)

    def test_private_targets_rejected_by_real_proxy(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': 'http://download-proxy:8080'}))
        for target in ('http://backend/', 'http://127.0.0.1/', 'http://169.254.169.254/'):
            with self.subTest(target=target), self.assertRaises(urllib.error.HTTPError) as ctx:
                opener.open(target, timeout=10)
            self.assertEqual(ctx.exception.code, 403)

    def test_public_https_through_real_proxy(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({
            'http': 'http://download-proxy:8080', 'https': 'http://download-proxy:8080'}))
        with opener.open('https://raw.githubusercontent.com/naum-mm/box4one/main/README.md', timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b'BOX4ONE', response.read(16384))

    def test_existing_worker_processes_and_fails_unsafe_web_job(self):
        _, created = request('POST', '/api/jobs', {
            'source_url': 'http://backend/', 'media_type': 'video', 'quality': '480'},
            {'X-Download-Access': 'download-test-only'})
        code = created['job_code']
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            _, job = request('GET', '/api/jobs/' + code)
            if job['status'] == 'failed':
                return
            self.assertIn(job['status'], ('queued', 'downloading', 'processing'))
            time.sleep(2)
        self.fail('Isolated worker did not finish its test job')


if __name__ == '__main__':
    unittest.main(verbosity=2)
