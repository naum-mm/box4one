"""Real MariaDB: signed browser identities and shared, atomic admission limits."""
import concurrent.futures
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.database import SessionLocal
from app.models import Job, File
from app.security import CLIENT_COOKIE
from app.telegram_models import TelegramRequest, TelegramDelivery
from app.telegram_migrate import migrate
from app.web_models import WebClient, WebRequest


class WebLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate()

    def setUp(self):
        self.env = patch.dict(os.environ, MAX_ACTIVE_JOBS_PER_USER='1', MAX_JOB_GB='8', JOB_TTL_MINUTES='60')
        self.env.start(); self.addCleanup(self.env.stop)
        with SessionLocal.begin() as db:
            db.query(TelegramDelivery).delete(); db.query(TelegramRequest).delete()
            db.query(WebRequest).delete(); db.query(File).delete(); db.query(Job).delete(); db.query(WebClient).delete()
        self.client = TestClient(app, base_url='https://testserver')
        self.headers = {'X-Download-Access': 'download-test-only'}
        self.payload = {'source_url': 'https://example.com/video', 'media_type': 'video', 'quality': '720'}

    def create(self, client=None):
        return (client or self.client).post('/api/jobs', headers=self.headers, json=self.payload)

    def test_limits_are_runtime_values_and_cookie_is_private(self):
        r = self.client.get('/api/limits')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), dict(max_job_gb=8.0, job_ttl_minutes=60,
            max_active_jobs_per_user=1, video_playlist_limit=50, audio_playlist_limit=100))
        for attribute in ('HttpOnly', 'Secure', 'SameSite=lax', 'Path=/'):
            self.assertIn(attribute, r.headers['set-cookie'])
        self.assertNotIn('activation', r.text)

    def test_one_active_job_per_browser_and_quota_released_on_completion(self):
        self.client.get('/api/limits')
        first = self.create(); self.assertEqual(first.status_code, 201)
        self.assertEqual(self.create().status_code, 429)
        with SessionLocal.begin() as db:
            db.scalar(select(Job).where(Job.job_code == first.json()['job_code'])).status = 'completed'
        self.assertEqual(self.create().status_code, 201)

    def test_separate_browsers_each_get_one_job(self):
        other = TestClient(app, base_url='https://testserver')
        self.client.get('/api/limits'); other.get('/api/limits')
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create(other).status_code, 201)
        self.assertEqual(self.create().status_code, 429)
        self.assertEqual(self.create(other).status_code, 429)

    def test_concurrent_requests_admit_one(self):
        self.client.get('/api/limits')
        cookie = self.client.cookies.get(CLIENT_COOKIE)
        def invoke(_):
            with TestClient(app, base_url='https://testserver') as client:
                return client.post('/api/jobs', headers=self.headers | {'Cookie': CLIENT_COOKIE+'='+cookie}, json=self.payload).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(invoke, range(6)))
        self.assertEqual(codes.count(201), 1, codes)
        self.assertEqual(codes.count(429), 5, codes)

    def test_browser_identity_does_not_bypass_access_control(self):
        self.client.get('/api/limits')
        self.assertEqual(self.client.post('/api/jobs', json=self.payload).status_code, 403)

    def test_worker_completion_uses_shared_sixty_minute_retention(self):
        self.client.get('/api/limits')
        code = self.create().json()['job_code']
        headers = {'X-Worker-Token': 'worker-test-token'}
        self.assertEqual(self.client.post('/api/worker/jobs/next', headers=headers).json()['job_code'], code)
        response = self.client.post(f'/api/worker/jobs/{code}/complete', headers=headers, json={'files': [
            dict(filename='clip.mp4', relative_path='clip.mp4', size_bytes=4, media_type='video')]})
        self.assertEqual(response.status_code, 200, response.text)
        with SessionLocal() as db:
            job = db.scalar(select(Job).where(Job.job_code == code))
            self.assertAlmostEqual((job.expires_at-job.completed_at).total_seconds(), 3600, delta=2)
