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
from app.telegram_models import TelegramUser, TelegramRequest, TelegramRuntime, TelegramActivation, TelegramDelivery
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
            db.query(TelegramDelivery).delete()
            db.query(TelegramActivation).delete()
            db.query(TelegramRequest).delete()
            db.query(File).delete()
            db.query(Job).delete()
            db.query(TelegramRuntime).delete()
            db.add(TelegramRuntime(id=1))
        for uid in (UID, OTHER):
            with SessionLocal.begin() as db:
                if db.get(TelegramUser, uid):
                    db.get(TelegramUser, uid).enabled = True
            response = self.call('POST', '/activate', uid, json={'word': 'REDACTED_ACTIVATION_WORD'})
            self.assertTrue(response.json()['activated'])
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
        files = self.call('GET', f'/jobs/{code}').json()['files']
        self.assertTrue(all(file['media_type'] == 'video' for file in files))
        return files

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

    def test_cross_user_isolation_in_telegram(self):
        code = self.create()
        file = self.complete(code)[0]
        for method, path in [('GET', f'/jobs/{code}'), ('GET', f'/jobs/{code}/files/{file["file_id"]}'),
                             ('POST', f'/jobs/{code}/links/{file["file_id"]}')]:
            self.assertEqual(self.call(method, path, OTHER).status_code, 404)
        self.assertEqual(self.call('GET', '/jobs', OTHER).json()['jobs'], [])

    def test_web_and_telegram_jobs_share_queue_and_detail(self):
        telegram_code = self.create()
        created = self.client.post('/api/jobs', json={
            'source_url': 'https://example.com/web', 'media_type': 'audio', 'quality': '128'},
            headers={'X-Download-Access': 'download-test-only'})
        self.assertEqual(created.status_code, 201)
        web_code = created.json()['job_code']
        self.assertEqual(self.client.get('/api/queue').status_code, 403)
        queue = self.client.get('/api/queue', headers={'X-Queue-Access': 'queue-test-only'})
        self.assertEqual(queue.status_code, 200)
        jobs_in_queue = {j['job_code']: j for j in queue.json()['jobs']}
        self.assertEqual(set(jobs_in_queue), {telegram_code, web_code})
        self.assertEqual(queue.json()['queued'], 2)
        self.assertEqual(jobs_in_queue[telegram_code]['queue_position'], 1)
        self.assertEqual(jobs_in_queue[web_code]['queue_position'], 2)
        for code in (telegram_code, web_code):
            detail = self.client.get(f'/api/jobs/{code}')
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()['job_code'], code)
            self.assertNotIn('telegram_id', detail.json())

    def test_history_stops_at_ten_without_losing_older_pending_jobs(self):
        codes = []
        with SessionLocal.begin() as db:
            for index in range(30):
                job = Job(job_code=f'{index:010d}', source_url='https://example.com/playlist',
                          media_type='video', quality='480', status='queued' if index == 0 else 'failed')
                db.add(job); db.flush()
                db.add(TelegramRequest(id=f'{index:032x}', telegram_id=UID,
                    source_message_id=index + 1, source_url=job.source_url, job_id=job.id,
                    terminal_notified=index != 0))
                codes.append(job.job_code)
        queue = self.client.get('/api/queue?page=99', headers={'X-Queue-Access': 'queue-test-only'}).json()
        self.assertEqual((queue['page'], queue['pages'], queue['total'], queue['page_size']), (2, 2, 30, 25))
        self.assertEqual([j['job_code'] for j in queue['jobs']], codes[:5][::-1])
        self.assertEqual(queue['queued'], 1)
        first_page = self.client.get('/api/queue', headers={'X-Queue-Access': 'queue-test-only'}).json()
        self.assertEqual([j['job_code'] for j in first_page['jobs']], codes[-25:][::-1])
        history = self.call('GET', '/jobs?limit=100').json()
        self.assertEqual([j['job_code'] for j in history['jobs']], codes[-10:][::-1])
        self.assertIsNone(history['next'])
        with patch.dict(os.environ, {'TELEGRAM_HISTORY_LIMIT': '20'}):
            self.assertEqual(len(self.call('GET', '/jobs').json()['jobs']), 20)
        pending = self.call('GET', '/jobs?pending=true&limit=100').json()
        self.assertEqual([j['job_code'] for j in pending['jobs']], [codes[0]])
        self.assertEqual(self.client.get(f'/api/jobs/{codes[0]}').status_code, 200)

    def test_failure_reason_is_classified_without_raw_upstream_details(self):
        code = self.create()
        for error, reason in [('Задание остановлено: превышен предел размера задания, включая временные файлы.', 'job_size_limit'),
                              ('Playlist limit exceeded: maximum 50 video items', 'playlist_limit'),
                              ('Upstream private diagnostic https://example.com/secret', None)]:
            with SessionLocal.begin() as db:
                job = db.scalar(select(Job).where(Job.job_code == code))
                job.status = 'failed'; job.error_message = error
            response = self.call('GET', f'/jobs/{code}')
            self.assertEqual(response.json()['failure_reason'], reason)
            self.assertNotIn(error, response.text)

    def test_telegram_files_and_zip_work_through_web_interface(self):
        import io
        import zipfile
        code = self.create()
        files = self.complete(code, (4, 4))
        detail = self.client.get(f'/api/jobs/{code}')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()['files']), 2)
        for file in files:
            response = self.client.get(f'/api/jobs/{code}/files/{file["file_id"]}/download')
            self.assertEqual(response.status_code, 200)
            self.assertIn(code, response.headers['x-accel-redirect'])
        response = self.client.get(f'/api/jobs/{code}/archive')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(sorted(archive.namelist()), ['0.mp4', '1.mp4'])
            self.assertTrue(all(archive.read(name) == b'data' for name in archive.namelist()))
        with SessionLocal.begin() as db:
            db.scalar(select(Job).where(Job.job_code == code)).expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.assertEqual(self.client.get(f'/api/jobs/{code}/archive').status_code, 410)
        self.assertEqual(self.client.get(f'/api/jobs/{code}/files/{files[0]["file_id"]}/download').status_code, 410)

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

    def test_video_display_dimensions_are_in_file_details(self):
        from test_mp4_metadata import video
        code = self.create()
        file = self.complete(code)[0]
        (jobs.JOBS_ROOT / code / file['filename']).write_bytes(video(854, 480))
        details = self.call('GET', f'/jobs/{code}').json()['files'][0]
        self.assertEqual((details['width'], details['height']), (854, 480))

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
        with SessionLocal.begin() as db:
            db.get(TelegramUser, UID).enabled = False
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


    def test_public_activation_persistent_and_no_allowlist(self):
        uid = 999000123
        with patch.dict(os.environ, TELEGRAM_ALLOWED_USERS=''):
            self.assertEqual(self.call('GET', '/access', uid).json(), {'activated': False})
            self.assertEqual(self.call('GET', '/jobs', uid).status_code, 403)
            self.assertFalse(self.call('POST', '/activate', uid, json={'word': 'WRONG'}).json()['activated'])
            self.assertTrue(self.call('POST', '/activate', uid, json={'word': 'REDACTED_ACTIVATION_WORD'}).json()['activated'])
            migrate()
            with TestClient(app) as restarted:
                self.assertTrue(restarted.get('/api/integrations/telegram/access', headers=self.headers(uid)).json()['activated'])
            self.assertEqual(self.call('GET', '/jobs', uid).status_code, 200)
            self.assertIn(uid, self.call('GET', '/users').json()['users'])

    def test_activation_attempts_bounded_persistent_and_reset(self):
        uid = 999000124
        for _ in range(5):
            self.assertEqual(self.call('POST', '/activate', uid, json={'word': 'WRONG'}).status_code, 200)
        migrate()
        self.assertEqual(self.call('POST', '/activate', uid, json={'word': 'REDACTED_ACTIVATION_WORD'}).status_code, 429)
        with SessionLocal.begin() as db:
            db.get(TelegramActivation, uid).window_started = datetime.utcnow() - timedelta(minutes=11)
        self.assertTrue(self.call('POST', '/activate', uid, json={'word': 'REDACTED_ACTIVATION_WORD'}).json()['activated'])

    def test_delivery_claim_serialized_and_durable_after_ambiguous_send(self):
        code = self.create(); file = self.complete(code)[0]
        path = f"/jobs/{code}/files/{file['file_id']}/delivery"
        def invoke(_):
            return self.call('POST', path).json()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(invoke, range(4)))
        self.assertEqual(sum(c['send'] for c in claims), 1)
        claim = next(c for c in claims if c['send'])
        self.assertTrue(claim['local_uri'].startswith(jobs.JOBS_ROOT.as_uri()))
        self.assertEqual(claim['size_bytes'], 4)
        self.assertEqual(self.call('POST', path, OTHER).status_code, 404)
        # A restart while sending must never claim another upload.
        migrate()
        self.assertEqual(self.call('POST', path).json(), {'send': False, 'state': 'sending'})
        self.assertEqual(self.call('PUT', path, json={'claim': claim['claim'], 'state': 'sent', 'message_id': 42}).status_code, 200)
        self.assertEqual(self.call('POST', path).json(), {'send': False, 'state': 'sent'})
        self.assertEqual(self.call('PUT', path, json={'claim': 'f'*32, 'state': 'sent'}).status_code, 404)
