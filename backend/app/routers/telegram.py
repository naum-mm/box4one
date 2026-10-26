"""Internal API. The token has no worker/admin permissions."""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.mysql import insert
from app.database import get_db
from app.models import Job, File
from app.mp4_metadata import video_dimensions
from app.schemas import JobCreate
from app.telegram_models import TelegramRequest, TelegramUser, TelegramRuntime, TelegramActivation, TelegramDelivery
from app.url_safety import validate_url
from app.routers import jobs
from app.limits import max_active_jobs

router = APIRouter(prefix='/api/integrations/telegram')
download_router = APIRouter(prefix='/api/telegram-download')
FORMATS = {'v1080': ('video', '1080'), 'v720': ('video', '720'), 'v480': ('video', '480'),
           'a320': ('audio', '320'), 'a128': ('audio', '128')}
ACTIVE = ('queued', 'downloading', 'processing')


def token():
    value = os.getenv('TELEGRAM_INTERNAL_TOKEN', '')
    if len(value) < 32 or value == os.getenv('WORKER_API_TOKEN'):
        raise HTTPException(503, 'Интеграция отключена')
    return value


def authorize(x_telegram_token: str = Header(default='')):
    if not secrets.compare_digest(x_telegram_token.encode(), token().encode()):
        raise HTTPException(401, 'Доступ запрещён')


def user(x_telegram_user: int = Header(gt=0, le=2**63-1), _=Depends(authorize), db: Session = Depends(get_db)):
    row = db.scalar(select(TelegramUser).where(TelegramUser.telegram_id == x_telegram_user).with_for_update())
    activation = db.get(TelegramActivation, x_telegram_user)
    if row is None or not row.enabled or activation is None or activation.activated_at is None:
        raise HTTPException(403, 'Требуется активация')
    return row


class Activation(BaseModel):
    word: str = Field(min_length=1, max_length=128)


def identity(x_telegram_user: int = Header(gt=0, le=2**63-1), _=Depends(authorize)):
    return x_telegram_user


@router.get('/access')
def access(uid=Depends(identity), db: Session = Depends(get_db)):
    row = db.get(TelegramUser, uid)
    activation = db.get(TelegramActivation, uid)
    return {'activated': bool(row and row.enabled and activation and activation.activated_at)}


@router.post('/activate')
def activate(request: Activation, uid=Depends(identity), db: Session = Depends(get_db)):
    word = os.getenv('TELEGRAM_ACTIVATION_WORD', '')
    if not word:
        raise HTTPException(503, 'Активация отключена')
    now = datetime.utcnow()
    # INSERT IGNORE + row lock serializes concurrent attempts on MariaDB.
    db.execute(insert(TelegramUser).values(telegram_id=uid, enabled=True).prefix_with('IGNORE'))
    owner = db.scalar(select(TelegramUser).where(TelegramUser.telegram_id == uid).with_for_update())
    if not owner.enabled:
        raise HTTPException(403, 'Доступ запрещён')
    row = db.get(TelegramActivation, uid)
    if row is None:
        row = TelegramActivation(telegram_id=uid, attempts=0, window_started=now)
        db.add(row)
    if row.activated_at:
        db.commit()
        return {'activated': True}
    if row.window_started <= now - timedelta(minutes=10):
        row.attempts, row.window_started = 0, now
    if row.attempts >= 5:
        raise HTTPException(429, 'Повторите через 10 минут')
    row.attempts += 1
    correct = hmac.compare_digest(request.word.encode(), word.encode())
    if correct:
        row.activated_at = now
    db.commit()  # Wrong attempts must survive a restart as well.
    return {'activated': correct}


@router.get('/users', dependencies=[Depends(authorize)])
def active_users(after: int = 0, db: Session = Depends(get_db)):
    ids = list(db.scalars(select(TelegramUser.telegram_id).join(TelegramActivation).where(
        TelegramUser.enabled.is_(True), TelegramActivation.activated_at.is_not(None),
        TelegramUser.telegram_id > after).order_by(TelegramUser.telegram_id).limit(100)))
    return {'users': ids, 'next': ids[-1] if len(ids) == 100 else None}


def owned(code, owner, db):
    row = db.scalar(select(TelegramRequest).join(Job).where(
        Job.job_code == code, TelegramRequest.telegram_id == owner.telegram_id))
    if row is None:
        raise HTTPException(404, 'Задание не найдено')
    return row, db.get(Job, row.job_id)


def describe_file(job, record):
    result = dict(file_id=record.id, filename=record.filename,
                  size_bytes=record.size_bytes, media_type=record.media_type)
    if record.media_type == 'video' and record.filename.lower().endswith('.mp4'):
        try:
            path = jobs.resolve_job_file(job.job_code, record.relative_path)
            result.update(video_dimensions(path))
        except HTTPException:
            pass  # A missing/expired file must not hide the job's status.
    return result


def describe(row, job):
    state = jobs.get_effective_status(job)
    failure_reason = None
    if state == 'failed':
        error = job.error_message or ''
        if error.startswith('Задание остановлено: превышен предел размера задания'):
            failure_reason = 'job_size_limit'
        elif error.startswith('Playlist limit exceeded:'):
            failure_reason = 'playlist_limit'
    return dict(job_code=job.job_code, status=state, completed_items=job.completed_items,
                total_items=job.total_items, message_id=row.message_id, last_text=row.last_text,
                terminal_notified=row.terminal_notified,
                failure_reason=failure_reason,
                files=[describe_file(job, f)
                       for f in sorted(job.files, key=lambda f: f.id) if f.status == 'ready']
                      if state == 'completed' else [])


class Draft(BaseModel):
    source_url: str = Field(max_length=2048, min_length=8)
    source_message_id: int = Field(gt=0, le=2**63-1)


class Create(BaseModel):
    message_id: int = Field(gt=0, le=2**63-1)
    draft_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    format: str = Field(pattern=r'^(v1080|v720|v480|a320|a128)$')


class Notification(BaseModel):
    message_id: int = Field(gt=0, le=2**63-1)
    text: str = Field(max_length=512)
    terminal: bool = False


@router.post('/drafts')
def draft(request: Draft, owner=Depends(user), db: Session = Depends(get_db)):
    validate_url(request.source_url)
    db.refresh(owner, with_for_update=True)
    row = db.scalar(select(TelegramRequest).where(TelegramRequest.telegram_id == owner.telegram_id,
                        TelegramRequest.source_message_id == request.source_message_id))
    if row is None:
        # Bound draft growth. Replays of old messages never cause another job.
        count = db.scalar(select(func.count()).select_from(TelegramRequest).where(
            TelegramRequest.telegram_id == owner.telegram_id,
            TelegramRequest.created_at > datetime.utcnow() - timedelta(hours=1)))
        if count >= int(os.getenv('TELEGRAM_MAX_DRAFTS_PER_HOUR', '60')):
            raise HTTPException(429, 'Слишком много ссылок. Повторите позже')
        row = TelegramRequest(id=secrets.token_hex(16), telegram_id=owner.telegram_id,
                              source_message_id=request.source_message_id, source_url=request.source_url)
        db.add(row)
        db.commit()
    return {'draft_id': row.id}


@router.post('/jobs', status_code=201)
def create(request: Create, owner=Depends(user), db: Session = Depends(get_db)):
    # Lock the user BEFORE all reads: serializes choices, retries and per-user quota.
    db.refresh(owner, with_for_update=True)
    row = db.scalar(select(TelegramRequest).where(TelegramRequest.id == request.draft_id,
                                      TelegramRequest.telegram_id == owner.telegram_id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Ссылка не найдена')
    if row.job_id:
        return describe(row, db.get(Job, row.job_id))
    if row.created_at < datetime.utcnow() - timedelta(minutes=int(os.getenv('TELEGRAM_DRAFT_TTL_MINUTES', '60'))):
        raise HTTPException(410, 'Отправьте ссылку заново')
    count = db.scalar(select(func.count()).select_from(TelegramRequest).join(Job).where(
        TelegramRequest.telegram_id == owner.telegram_id, Job.status.in_(ACTIVE)))
    if count >= max_active_jobs():
        raise HTTPException(429, 'Достигнут лимит активных заданий')
    validate_url(row.source_url)
    media, quality = FORMATS[request.format]
    payload = JobCreate(source_url=row.source_url, media_type=media, quality=quality)
    job = jobs.enqueue_job(db, payload)
    row.message_id = request.message_id
    row.job_id = job.id
    db.commit()
    return describe(row, job)


@router.get('/jobs')
def listing(pending: bool = False, limit: int | None = Query(None, ge=1, le=100),
            before: str | None = Query(None, pattern=r'^[a-f0-9]{32}$'),
            owner=Depends(user), db: Session = Depends(get_db)):
    query = select(TelegramRequest).where(TelegramRequest.telegram_id == owner.telegram_id,
                                        TelegramRequest.job_id.is_not(None))
    if pending:
        limit = limit or 100
        query = query.where(TelegramRequest.terminal_notified.is_(False))
    else:
        history_limit = max(1, min(100, int(os.getenv('TELEGRAM_HISTORY_LIMIT', '10'))))
        limit = min(limit or history_limit, history_limit)
    if before and pending:
        query = query.where(TelegramRequest.id < before)
    rows = db.scalars(query.order_by(TelegramRequest.id.desc() if pending else TelegramRequest.job_id.desc()).limit(limit)).all()
    return {'jobs': [describe(row, db.get(Job, row.job_id)) for row in rows],
            'next': rows[-1].id if pending and len(rows) == limit else None}


@router.get('/jobs/{code}')
def detail(code: str, owner=Depends(user), db: Session = Depends(get_db)):
    return describe(*owned(code, owner, db))


@router.put('/jobs/{code}/notification')
def notified(code: str, request: Notification, owner=Depends(user), db: Session = Depends(get_db)):
    row, job = owned(code, owner, db)
    row.message_id = request.message_id
    row.last_text = request.text
    row.terminal_notified = request.terminal and jobs.get_effective_status(job) not in ACTIVE
    db.commit()
    return {'ok': True}


def ready_file(code, file_id, owner, db):
    _, job = owned(code, owner, db)
    state = jobs.get_effective_status(job)
    if state != 'completed':
        raise HTTPException(410 if state == 'expired' else 409, 'Файл недоступен')
    record = db.scalar(select(File).where(File.id == file_id, File.job_id == job.id, File.status == 'ready'))
    if record is None:
        raise HTTPException(404, 'Файл не найден')
    return job, record


@router.get('/jobs/{code}/files/{file_id}')
def internal_file(code: str, file_id: int, owner=Depends(user), db: Session = Depends(get_db)):
    job, record = ready_file(code, file_id, owner, db)
    return FileResponse(jobs.resolve_job_file(code, record.relative_path), filename=record.filename,
                        headers={'Cache-Control': 'no-store'})


@router.post('/jobs/{code}/links/{item}')
def link(code: str, item: str, owner=Depends(user), db: Session = Depends(get_db)):
    row, job = owned(code, owner, db)
    if item == 'archive':
        if jobs.get_effective_status(job) != 'completed' or len(job.files) < 2:
            raise HTTPException(409, 'Архив недоступен')
    elif item.isdecimal():
        ready_file(code, int(item), owner, db)
    else:
        raise HTTPException(404, 'Файл не найден')
    base = os.getenv('TELEGRAM_PUBLIC_URL', '').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
        raise HTTPException(503, 'HTTPS-ссылка не настроена')
    if job.expires_at is None:
        raise HTTPException(410, 'Файл недоступен')
    expires = min(int(time.time()) + int(os.getenv('TELEGRAM_LINK_TTL_SECONDS', '600')),
                  int(job.expires_at.replace(tzinfo=timezone.utc).timestamp()))
    payload = f'{owner.telegram_id}:{code}:{item}:{expires}'
    signature = hmac.new(token().encode(), ('download:' + payload).encode(), hashlib.sha256).hexdigest()
    return {'url': f'{base}/api/telegram-download/{payload}/{signature}'}


@download_router.get('/{payload}/{signature}')
def download(payload: str, signature: str, db: Session = Depends(get_db)):
    expected = hmac.new(token().encode(), ('download:' + payload).encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        raise HTTPException(403, 'Ссылка недействительна')
    try:
        uid, code, item, expires = payload.split(':')
        if int(expires) <= time.time():
            raise ValueError()
        uid = int(uid)
    except ValueError:
        raise HTTPException(410, 'Ссылка истекла') from None
    owner = user(uid, None, db)
    _, job = owned(code, owner, db)
    if item == 'archive':
        return jobs.stream_archive(job, db)
    job, record = ready_file(code, int(item), owner, db)
    return FileResponse(jobs.resolve_job_file(code, record.relative_path), filename=record.filename,
                        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


class Runtime(BaseModel):
    owner: str = Field(pattern=r'^[a-f0-9]{32}$')
    offset: int | None = Field(None, ge=0)


@router.post('/runtime', dependencies=[Depends(authorize)])
def runtime(request: Runtime, db: Session = Depends(get_db)):
    state = db.scalar(select(TelegramRuntime).where(TelegramRuntime.id == 1).with_for_update())
    if state is None:
        raise HTTPException(503, 'Требуется миграция')
    if state.owner != request.owner and state.lease_until > datetime.utcnow():
        raise HTTPException(409, 'Бот уже запущен')
    state.owner = request.owner
    state.lease_until = datetime.utcnow() + timedelta(seconds=90)
    if request.offset is not None:
        state.offset = max(state.offset, request.offset)
    db.commit()
    return {'offset': state.offset}


class DeliveryResult(BaseModel):
    claim: str = Field(pattern=r'^[a-f0-9]{32}$')
    state: str = Field(pattern=r'^(sent|fallback|uncertain)$')
    message_id: int | None = Field(None, gt=0)


@router.post('/jobs/{code}/files/{file_id}/delivery')
def claim_delivery(code: str, file_id: int, owner=Depends(user), db: Session = Depends(get_db)):
    job, record = ready_file(code, file_id, owner, db)
    row = db.get(TelegramDelivery, (owner.telegram_id, file_id))
    if row and row.state != 'fallback':
        return {'send': False, 'state': row.state}
    path = jobs.resolve_job_file(code, record.relative_path)
    if row is None:
        row = TelegramDelivery(telegram_id=owner.telegram_id, file_id=file_id,
                               claim=secrets.token_hex(16), state='sending')
        db.add(row)
    else:
        # fallback means the size check skipped Telegram entirely. A later manual
        # click may now fit Local API after a cloud-to-local migration.
        row.state, row.claim = 'sending', secrets.token_hex(16)
        row.updated_at = datetime.utcnow()
    db.commit()  # Persist BEFORE contacting Telegram; ambiguous attempts never replay.
    return {'send': True, 'claim': row.claim, 'size_bytes': path.stat().st_size,
            'local_uri': path.as_uri()}


@router.put('/jobs/{code}/files/{file_id}/delivery')
def finish_delivery(code: str, file_id: int, request: DeliveryResult,
                    owner=Depends(user), db: Session = Depends(get_db)):
    _, job = owned(code, owner, db)
    record = db.get(File, file_id)
    row = db.get(TelegramDelivery, (owner.telegram_id, file_id))
    if record is None or record.job_id != job.id or row is None or row.claim != request.claim:
        raise HTTPException(404, 'Отправка не найдена')
    if row.state == 'sending':
        row.state, row.message_id = request.state, request.message_id
        row.updated_at = datetime.utcnow()
    db.commit()
    return {'ok': True}


@router.delete('/runtime/{owner}', dependencies=[Depends(authorize)])
def release_runtime(owner: str, db: Session = Depends(get_db)):
    state = db.scalar(select(TelegramRuntime).where(TelegramRuntime.id == 1).with_for_update())
    if state and state.owner == owner:
        state.lease_until = datetime.utcnow()
        db.commit()
    return {'ok': True}
