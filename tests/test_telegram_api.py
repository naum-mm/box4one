"""Real MariaDB, synthetic data only. Run with tests/compose.telegram.yaml."""
import concurrent.futures
import os
from pathlib import Path
import shutil
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.main import app
from app.database import SessionLocal, engine
from app.models import Job, File
from app.telegram_models import TelegramUser, TelegramRequest, TelegramRuntime
from app.telegram_migrate import migrate
from app.routers import jobs

TOKEN = os.environ['TELEGRAM_INTERNAL_TOKEN']
UID = 10000000001
OTHER = 10000000002


class TelegramAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate()

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=True)
        with SessionLocal.begin() as db:
            db.query(TelegramRequest).delete()
            db.query(File).delete()
            db.query(Job).delete()
            db.query(TelegramRuntime).delete()
            db.add(TelegramRuntime(id=1))
        self.dns = patch('app.url_safety.getaddrinfo', return_value=[
            (2, 1, 6, '', ('93.184.216.34', 443))])
        self.dns.start()
        self.addCleanup(self.dns.stop)
        shutil.rmtree(jobs.JOBS_ROOT, ignore_errors=True)
        jobs.JOBS_ROOT.mkdir(parents=True)

    def headers(self, uid=UID):
        return {'X-Telegram-Token': TOKEN, 'X-Telegram-User': str(uid)}

    def call(self, method, path, uid=UID, **kwargs):
        return self.client.request(method, '/api/integrations/telegram' + path,
                                   headers=self.headers(uid), **kwargs)

    def draft(self, message=1, uid=UID, url='https://example.com/video'):
        r = self.call('POST', '/drafts', uid, json={'source_url': url, 'source_message_id': message})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['draft_id']

    def create(self, message=1, uid=UID, fmt='v720'):
        draft = self.draft(message, uid)
        r = self.call('POST', '/jobs', uid, json={'draft_id': draft, 'format': fmt, 'message_id': 5})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()['job_code']

    def complete(self, code, sizes=(4,)):
        with SessionLocal.begin() as db:
            job = db.scalar(select(Job).where(Job.job_code == code))
            job.status = 'completed'
            job.expires_at = datetime.utcnow() + timedelta(minutes=10)
            root = jobs.JOBS_ROOT / code
            root.mkdir(exist_ok=True)
            for i, size in enumerate(sizes):
                name = f'{i}.mp4'
                (root / name).write_bytes(b'data')
                db.add(File(job_id=job.id, filename=name, relative_path=name, size_bytes=size, media_type='video'))
        return self.call('GET', f'/jobs/{code}').json()['files']

    def test_migration_repeatable_preserves_jobs_and_tables(self):
        code = self.create()
        migrate(); migrate()
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM jobs')).scalar(), 1)
            columns = conn.execute(text('SHOW COLUMNS FROM telegram_users')).all()
            self.assertIn('bigint', str(columns).lower())
        self.assertEqual(self.call('GET', f'/jobs/{code}').status_code, 200)

    def test_token_separation_and_disabled_integration(self):
        self.assertEqual(self.client.get('/api/integrations/telegram/jobs',
            headers={'X-Telegram-Token': 'worker-test-token', 'X-Telegram-User': str(UID)}).status_code, 401)
        self.assertEqual(self.client.post('/api/worker/jobs/next', headers={'X-Worker-Token': TOKEN}).status_code, 401)
        with patch.dict(os.environ, TELEGRAM_INTERNAL_TOKEN=''):
            self.assertEqual(self.call('GET', '/jobs').status_code, 503)

    def test_allowlist_on_every_action(self):
        for method, path, data in [('GET', '/jobs', None), ('POST', '/drafts', {'source_url': 'https://example.com', 'source_message_id': 1})]:
            self.assertEqual(self.call(method, path, 999, json=data).status_code, 403)
        with SessionLocal.begin() as db:
            db.get(TelegramUser, UID).enabled = False
        self.assertEqual(self.call('GET', '/jobs').status_code, 403)
        migrate()

    def test_all_formats_use_existing_worker_queue(self):
        for i, fmt in enumerate(('v1080', 'v720', 'v480', 'a320', 'a128')):
            code = self.create(i + 1, fmt=fmt)
            with SessionLocal.begin() as db:
                db.scalar(select(Job).where(Job.job_code == code)).status = 'failed'
        with SessionLocal() as db:
            self.assertEqual(db.query(Job).count(), 5)
            self.assertEqual(db.query(Job).filter_by(media_type='audio').count(), 2)

    def test_duplicate_update_and_callback(self):
        draft = self.draft()
        self.assertEqual(self.draft(), draft)
        codes = [self.call('POST', '/jobs', json={'draft_id': draft, 'format': fmt, 'message_id': 5}).json()['job_code']
                 for fmt in ('v720', 'v1080', 'a320')]
        self.assertEqual(len(set(codes)), 1)
        with SessionLocal() as db:
            self.assertEqual(db.query(Job).count(), 1)

    def test_concurrent_choices_create_one_job(self):
        draft = self.draft()
        def invoke(_):
            with TestClient(app) as client:
                return client.post('/api/integrations/telegram/jobs', headers=self.headers(),
                    json={'draft_id': draft, 'format': 'v720', 'message_id': 5})
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(invoke, range(6)))
        self.assertTrue(all(r.status_code == 201 for r in results), [r.text for r in results])
        self.assertEqual(len({r.json()['job_code'] for r in results}), 1)

    def test_concurrent_quota_is_atomic(self):
        drafts = [self.draft(i) for i in range(1, 7)]
        def invoke(draft):
            with TestClient(app) as client:
                return client.post('/api/integrations/telegram/jobs', headers=self.headers(),
                    json={'draft_id': draft, 'format': 'v720', 'message_id': 5}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(invoke, drafts))
        self.assertEqual(statuses.count(201), 2, statuses)
        self.assertEqual(statuses.count(429), 4, statuses)

    def test_cross_user_isolation_and_public_bypass(self):
        code = self.create()
        file = self.complete(code)[0]
        for method, path in [('GET', f'/jobs/{code}'), ('GET', f'/jobs/{code}/files/{file["file_id"]}'),
                             ('POST', f'/jobs/{code}/links/{file["file_id"]}')]:
            self.assertEqual(self.call(method, path, OTHER).status_code, 404)
        self.assertEqual(self.call('GET', '/jobs', OTHER).json()['jobs'], [])
        for suffix in ['', '/archive', f'/files/{file["file_id"]}/download']:
            self.assertEqual(self.client.get(f'/api/jobs/{code}{suffix}').status_code, 404)

    def test_foreign_file_and_path_traversal(self):
        code = self.create(); file = self.complete(code)[0]
        self.assertEqual(self.call('GET', f'/jobs/{code}/files/999999').status_code, 404)
        with SessionLocal.begin() as db:
            db.get(File, file['file_id']).relative_path = '../secret'
        self.assertEqual(self.call('GET', f'/jobs/{code}/files/{file["file_id"]}').status_code, 500)

    def test_safe_signed_streaming_file_and_tampering(self):
        code = self.create(); file = self.complete(code)[0]
        link = self.call('POST', f'/jobs/{code}/links/{file["file_id"]}').json()['url']
        response = self.client.get(link)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'data')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get(link[:-1] + ('0' if link[-1] != '0' else '1')).status_code, 403)
        self.assertEqual(self.client.get(link.replace(str(UID), str(OTHER))).status_code, 403)

    def test_link_expiry_and_job_ttl(self):
        code = self.create(); file = self.complete(code)[0]
        link = self.call('POST', f'/jobs/{code}/links/{file["file_id"]}').json()['url']
        with patch('app.routers.telegram.time.time', return_value=time.time() + 1000):
            self.assertEqual(self.client.get(link).status_code, 410)
        with SessionLocal.begin() as db:
            db.scalar(select(Job).where(Job.job_code == code)).expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.assertEqual(self.call('GET', f'/jobs/{code}').json()['status'], 'expired')
        self.assertEqual(self.client.get(link).status_code, 410)
        self.assertEqual(self.call('GET', f'/jobs/{code}/files/{file["file_id"]}').status_code, 410)

    def test_signed_link_revoked_when_user_removed(self):
        code = self.create(); file = self.complete(code)[0]
        link = self.call('POST', f'/jobs/{code}/links/{file["file_id"]}').json()['url']
        with patch.dict(os.environ, TELEGRAM_ALLOWED_USERS=str(OTHER)):
            self.assertEqual(self.client.get(link).status_code, 403)

    def test_playlist_zip_stream(self):
        import io, zipfile
        code = self.create(); self.complete(code, (4, 4))
        link = self.call('POST', f'/jobs/{code}/links/archive').json()['url']
        r = self.client.get(link)
        self.assertEqual(r.status_code, 200, r.text)
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            self.assertEqual(archive.read('0.mp4'), b'data')

    def test_notification_and_restart_recovery(self):
        code = self.create()
        self.assertEqual(len(self.call('GET', '/jobs?pending=true').json()['jobs']), 1)
        self.complete(code)
        self.call('PUT', f'/jobs/{code}/notification', json={'message_id': 44, 'text': 'Готово', 'terminal': True})
        with TestClient(app) as restarted:
            result = restarted.get('/api/integrations/telegram/jobs?pending=true', headers=self.headers())
            self.assertEqual(result.json()['jobs'], [])
        self.assertEqual(self.call('GET', f'/jobs/{code}').json()['message_id'], 44)

    def test_runtime_offset_and_single_poller(self):
        one, two = 'a' * 32, 'b' * 32
        self.assertEqual(self.call('POST', '/runtime', json={'owner': one, 'offset': 12}).json()['offset'], 12)
        self.assertEqual(self.call('POST', '/runtime', json={'owner': two}).status_code, 409)
        with SessionLocal.begin() as db:
            db.get(TelegramRuntime, 1).lease_until = datetime.utcnow() - timedelta(seconds=1)
        self.assertEqual(self.call('POST', '/runtime', json={'owner': two, 'offset': 2}).json()['offset'], 12)

    def test_input_limits_and_validation(self):
        for url in ['https://example.com/' + 'a' * 2048, 'file:///etc/passwd', 'http://user:pass@example.com/', 'http://example.com:8000/']:
            self.assertEqual(self.call('POST', '/drafts', json={'source_url': url, 'source_message_id': 1}).status_code, 422)
        draft = self.draft()
        self.assertEqual(self.call('POST', '/jobs', json={'draft_id': draft, 'format': 'v320', 'message_id': 5}).status_code, 422)
        with patch('app.url_safety.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            self.assertEqual(self.call('POST', '/drafts', json={'source_url': 'http://localhost/', 'source_message_id': 2}).status_code, 422)

    def test_draft_ownership_and_expiry(self):
        draft = self.draft()
        payload = {'draft_id': draft, 'format': 'a128', 'message_id': 5}
        self.assertEqual(self.call('POST', '/jobs', OTHER, json=payload).status_code, 404)
        with SessionLocal.begin() as db:
            db.get(TelegramRequest, draft).created_at = datetime.utcnow() - timedelta(hours=2)
        self.assertEqual(self.call('POST', '/jobs', json=payload).status_code, 410)

    def test_web_and_worker_backwards_compatibility(self):
        r = self.client.post('/api/jobs', headers={'X-Download-Access': 'download-test-only'},
            json={'source_url': 'https://example.com/', 'media_type': 'video', 'quality': '720'})
        self.assertEqual(r.status_code, 201, r.text)
        code = r.json()['job_code']
        self.assertEqual(self.client.get(f'/api/jobs/{code}').status_code, 200)
        worker = {'X-Worker-Token': 'worker-test-token'}
        self.assertEqual(self.client.post('/api/worker/jobs/next', headers=worker).json()['job_code'], code)
        self.assertEqual(self.client.post(f'/api/worker/jobs/{code}/failed', headers=worker,
                                        json={'error_message': 'test failure'}).status_code, 200)
        self.assertEqual(self.client.get(f'/api/jobs/{code}').json()['status'], 'failed')
