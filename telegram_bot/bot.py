"""Long polling with durable backend cursor, drafts and notifications; no local DB."""
import asyncio
import logging
import os
import re
import secrets
import signal
import time
from contextlib import suppress
from pathlib import PurePath

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, LinkPreviewOptions, ReplyKeyboardMarkup, KeyboardButton

STATUSES = {'queued': 'В очереди', 'downloading': 'Загрузка', 'processing': 'Обработка',
            'completed': 'Готово', 'failed': 'Ошибка обработки. Попробуйте другую ссылку.',
            'expired': 'Срок хранения истёк'}
FORMATS = [('Видео 1080p', 'v1080'), ('Видео 720p', 'v720'), ('Видео 480p', 'v480'),
           ('MP3 320', 'a320'), ('MP3 128', 'a128')]
TERMINAL = {'completed', 'failed', 'expired'}


WELCOME = 'Добро пожаловать в BOX4ONE! Введите кодовое слово для активации. Его можно получить у владельца сервиса.'
MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text='🍿 СКАЧАТЬ')],
    [KeyboardButton(text='📋 Мои задания'), KeyboardButton(text='ℹ️ Информация')]], resize_keyboard=True)


def local_enabled():
    return os.getenv('TELEGRAM_LOCAL_API_ENABLED', 'false').lower() == 'true'


def make_bot():
    session = AiohttpSession(timeout=900 if local_enabled() else 90)
    if local_enabled():
        from urllib.parse import urlsplit
        base = os.getenv('TELEGRAM_LOCAL_API_URL', 'http://telegram-api:8081').rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme != 'http' or parsed.hostname != 'telegram-api' or parsed.port != 8081 or parsed.path:
            raise ValueError('Local API must use the private telegram-api:8081 service')
        session.api = TelegramAPIServer.from_base(base, is_local=True)
    return Bot(os.environ['TELEGRAM_BOT_TOKEN'], session=session)


def help_text(upload_limit):
    history = max(1, min(100, int(os.getenv('TELEGRAM_HISTORY_LIMIT', '10'))))
    size = float(os.getenv('MAX_JOB_GB', '8'))
    ttl = int(os.getenv('JOB_TTL_MINUTES', '60'))
    video = int(os.getenv('VIDEO_PLAYLIST_LIMIT', '50'))
    audio = int(os.getenv('AUDIO_PLAYLIST_LIMIT', '100'))
    active = int(os.getenv('MAX_ACTIVE_JOBS_PER_USER', os.getenv('TELEGRAM_MAX_ACTIVE_JOBS', '1')))
    return (
        'Отправьте одну ссылку на видео или плейлист и выберите формат.\n\n'
        f'- Лимит задания: {size:g}ГБ, включая временные файлы обработки.\n'
        f'- Плейлист: до {video} видео или {audio} аудиофайлов.\n'
        f'- Активных заданий на пользователя: до {active}. Следующее задание отправляйте после завершения текущего.\n'
        f'- Файлы хранятся {ttl} мин после завершения задания.\n'
        '- Ссылка действует не дольше срока хранения.\n'
        f'- Напрямую в Telegram: до {upload_limit / 1000000:g} МБ на файл; '
        'более крупные файлы и ZIP доступны по ссылке.\n\n'
        'Очередь обработки общая с сайтом.\n'
        '/status — последнее ваше задание\n'
        f'/myjobs — последние {history} ваших заданий\n'
        '/help — эта справка')



class APIError(Exception):
    def __init__(self, status):
        self.status = status

    @property
    def text(self):
        return {401: 'Доступ запрещён.', 403: 'Доступ запрещён.', 404: 'Задание или ссылка не найдены.',
                409: 'Задание ещё не готово.', 410: 'Срок действия истёк. Отправьте ссылку заново.',
                422: 'Отправьте корректную публичную HTTP/HTTPS-ссылку.',
                429: 'Достигнут лимит. Попробуйте позже.'}.get(self.status, 'Сервис временно недоступен. Повторите позже.')


class API:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=os.getenv('TELEGRAM_API_URL', 'http://backend:8000'),
            headers={'X-Telegram-Token': os.environ['TELEGRAM_INTERNAL_TOKEN']},
            timeout=30, follow_redirects=False, trust_env=False)

    async def call(self, method, path, uid=None, **kwargs):
        headers = {'X-Telegram-User': str(uid)} if uid is not None else {}
        try:
            response = await self.client.request(method, '/api/integrations/telegram' + path, headers=headers, **kwargs)
        except httpx.RequestError:
            raise APIError(503) from None
        if response.status_code >= 400:
            raise APIError(response.status_code)
        return response.json()


class BackendFile(InputFile):
    """Bounded chunks from authenticated backend, streamed into Telegram multipart."""
    def __init__(self, api, uid, code, file, limit):
        super().__init__(filename=file['filename'], chunk_size=65536)
        self.api, self.uid, self.code, self.file, self.limit = api, uid, code, file, limit

    async def read(self, bot):
        path = f"/api/integrations/telegram/jobs/{self.code}/files/{self.file['file_id']}"
        async with self.api.client.stream('GET', path, headers={'X-Telegram-User': str(self.uid)}) as response:
            if response.status_code != 200:
                raise APIError(response.status_code)
            total = 0
            async for chunk in response.aiter_bytes(self.chunk_size):
                total += len(chunk)
                if total > self.limit:
                    raise APIError(413)
                yield chunk


def text(job):
    status = STATUSES.get(job['status'], 'Обработка')
    if job['status'] == 'failed':
        status = {'job_size_limit': 'Превышен лимит размера задания, включая временные файлы. Уменьшите качество или размер плейлиста. Лимиты: /help.',
                  'playlist_limit': 'Превышен лимит элементов плейлиста. Лимиты: /help.'}.get(job.get('failure_reason'), status)
    return f"Задание {job['job_code']}: {status}\nФайлов: {job['completed_items']}/{job['total_items']}"


def keyboard(job):
    if job['status'] != 'completed':
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Скачать файлы',
        callback_data=f"files:{job['job_code']}:0")]])


class App:
    def __init__(self, api, bot, allowed=None):
        self.api, self.bot, self.allowed = api, bot, allowed
        self.send_lock = asyncio.Lock()
        self.last_chat = {}
        self.local = local_enabled()
        cap = 2000000000 if self.local else 49000000
        self.limit = min(max(int(os.getenv('TELEGRAM_LOCAL_UPLOAD_LIMIT_BYTES' if self.local else 'TELEGRAM_UPLOAD_LIMIT_BYTES', str(cap))), 1), cap)
        self.help = help_text(self.limit)
        self.chat_interval = max(1.1, float(os.getenv('TELEGRAM_CHAT_INTERVAL_SECONDS', '1.1')))
        self.send_interval = max(0.06, float(os.getenv('TELEGRAM_SEND_INTERVAL_SECONDS', '0.06')))
        self.router = Router()
        self.router.message.register(self.message)
        self.router.callback_query.register(self.callback)

    async def send(self, uid, operation, **kwargs):
        # Single global limiter; <=20 operations/s, <=1 operation/s per private chat.
        async with self.send_lock:
            await asyncio.sleep(max(0, self.last_chat.get(uid, 0) + self.chat_interval - time.monotonic()))
            for attempt in range(3):
                try:
                    result = await operation(**kwargs)
                    break
                except TelegramRetryAfter as exc:
                    if attempt == 2 or exc.retry_after > 30:
                        raise
                    await asyncio.sleep(exc.retry_after + 1)
            self.last_chat[uid] = time.monotonic()
            await asyncio.sleep(self.send_interval)
            return result

    async def say(self, uid, message, **kwargs):
        return await self.send(uid, self.bot.send_message, chat_id=uid, text=message,
                               link_preview_options=LinkPreviewOptions(is_disabled=True), **kwargs)

    async def message(self, message):
        if message.chat.type != 'private' or not message.from_user:
            return
        uid = message.from_user.id
        value = (message.text or '').strip()
        if len(value) > 2048:
            await self.say(uid, 'Сообщение слишком длинное. Максимум 2048 символов.')
            return
        try:
            access = await self.api.call('GET', '/access', uid)
            command = value.split('@', 1)[0].split(' ', 1)[0]
            if not access['activated']:
                if command in {'/start', '/help'}:
                    await self.say(uid, WELCOME)
                else:
                    if not value or len(value) > 128:
                        await self.say(uid, WELCOME)
                        return
                    activated = await self.api.call('POST', '/activate', uid, json={'word': value})
                    await self.say(uid, '✅ Доступ открыт. Выберите действие.' if activated['activated']
                                   else 'Кодовое слово неверно. Попробуйте ещё раз.',
                                   reply_markup=MENU if activated['activated'] else None)
                return
            result = await self.api.call('GET', '/jobs', uid)
            command = value.split('@', 1)[0].split(' ', 1)[0]
            if command in {'/start', '/help'} or value == 'ℹ️ Информация':
                await self.say(uid, ('Добро пожаловать в BOX4ONE!\n' if command == '/start' else '') + self.help, reply_markup=MENU)
            elif value in {'🍿 СКАЧАТЬ', '🎬 Скачать видео', '🎵 Скачать аудио'}:
                await self.say(uid, 'Отправьте ссылку на видео или плейлист, затем выберите формат.', reply_markup=MENU)
            elif command in {'/status', '/myjobs'} or value == '📋 Мои задания':
                entries = result['jobs'][:1] if command == '/status' else result['jobs']
                if not entries:
                    await self.say(uid, 'У вас пока нет заданий.')
                for job in entries:
                    await self.say(uid, text(job), reply_markup=keyboard(job))
            elif value.startswith(('http://', 'https://')) and not any(c.isspace() for c in value):
                draft = await self.api.call('POST', '/drafts', uid, json={
                    'source_url': value, 'source_message_id': message.message_id})
                keys = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label,
                    callback_data=f"new:{draft['draft_id']}:{fmt}")] for label, fmt in FORMATS])
                await self.say(uid, 'Выберите формат:', reply_markup=keys)
            else:
                await self.say(uid, self.help)
        except APIError as exc:
            if exc.status >= 500:
                raise
            await self.say(uid, exc.text)

    async def callback(self, callback):
        if not callback.message or callback.message.chat.type != 'private':
            return
        uid = callback.from_user.id
        if callback.message.chat.id != uid:
            await callback.answer('Доступ запрещён.')
            return
        with suppress(TelegramBadRequest):
            await callback.answer()
        try:
            parts = (callback.data or '').split(':')
            if len(parts) != 3:
                return
            action, code, item = parts
            pattern = r'[a-f0-9]{32}' if action == 'new' else r'[a-z0-9]{10}'
            if not re.fullmatch(pattern, code):
                return
            if action == 'new':
                job = await self.api.call('POST', '/jobs', uid, json={'draft_id': code,
                    'format': item, 'message_id': callback.message.message_id})
                await self.update(uid, job)
            elif action == 'files':
                job = await self.api.call('GET', f'/jobs/{code}', uid)
                if job['status'] != 'completed':
                    await self.say(uid, text(job))
                    return
                page = max(0, int(item))
                entries = job['files'][page:page + 8]
                rows = [[InlineKeyboardButton(text=f"Файл {page + i + 1}",
                          callback_data=f"file:{code}:{f['file_id']}")] for i, f in enumerate(entries)]
                if page + 8 < len(job['files']):
                    rows.append([InlineKeyboardButton(text='Далее', callback_data=f'files:{code}:{page+8}')])
                if len(job['files']) > 1:
                    rows.append([InlineKeyboardButton(text='ZIP по HTTPS-ссылке', callback_data=f'link:{code}:archive')])
                await self.say(uid, 'Выберите файл:\n' + '\n'.join(
                    f"{page+i+1}. {f['filename'][:200]}" for i, f in enumerate(entries)),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            elif action in {'file', 'link'}:
                job = await self.api.call('GET', f'/jobs/{code}', uid)
                file = next((f for f in job['files'] if str(f['file_id']) == item), None)
                if action == 'file' and file:
                    await self.deliver(uid, code, file)
                else:
                    await self.send_link(uid, code, item)

        except APIError as exc:
            if exc.status >= 500:
                raise
            await self.say(uid, exc.text)
        except ValueError:
            await self.say(uid, 'Кнопка недействительна. Используйте /status.')

    async def send_link(self, uid, code, item):
        link = await self.api.call('POST', f'/jobs/{code}/links/{item}', uid)
        await self.say(uid, 'Скачать по защищённой HTTPS-ссылке. Ссылка действует ограниченное время и до окончания хранения задания.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Скачать', url=link['url'])]]))

    async def deliver(self, uid, code, file):
        path = f"/jobs/{code}/files/{file['file_id']}/delivery"
        claim = await self.api.call('POST', path, uid)
        if not claim['send']:
            if claim['state'] == 'sent':
                await self.say(uid, '✅ Этот файл уже отправлен в чат.')
            else:
                await self.send_link(uid, code, str(file['file_id']))
            return
        state, message_id = 'fallback', None
        try:
            if max(file['size_bytes'], claim['size_bytes']) <= self.limit:
                # Local API reads its readonly bind mount; bot never opens the file.
                upload = claim['local_uri'] if self.local else BackendFile(self.api, uid, code, file, self.limit)
                suffix = PurePath(file['filename']).suffix.lower()
                if file.get('media_type') == 'video' and suffix == '.mp4':
                    dimensions = {key: file[key] for key in ('width', 'height')
                                  if isinstance(file.get(key), int) and 0 < file[key] <= 65535}
                    result = await self.send(uid, self.bot.send_video, chat_id=uid,
                        video=upload, supports_streaming=True, **dimensions)
                elif file.get('media_type') == 'audio' and suffix in {'.mp3', '.m4a'}:
                    result = await self.send(uid, self.bot.send_audio, chat_id=uid, audio=upload)
                else:
                    result = await self.send(uid, self.bot.send_document, chat_id=uid, document=upload)
                state, message_id = 'sent', result.message_id
        except (TelegramAPIError, APIError, httpx.HTTPError, OSError):
            # A network timeout may mean Telegram accepted the file. Never upload again.
            state = 'uncertain'
            logging.warning('Передача файла не подтверждена; используется HTTPS-ссылка')
        await self.api.call('PUT', path, uid, json={'claim': claim['claim'], 'state': state, 'message_id': message_id})
        if state != 'sent':
            await self.send_link(uid, code, str(file['file_id']))

    async def update(self, uid, job):
        value = text(job)
        if job['last_text'] == value and job['message_id']:
            return
        message_id = job['message_id']
        if message_id:
            try:
                await self.send(uid, self.bot.edit_message_text, chat_id=uid, message_id=message_id,
                                text=value, reply_markup=keyboard(job))
            except TelegramBadRequest as exc:
                if 'message is not modified' not in exc.message.lower():
                    # Deleted/inaccessible messages are recreated; failures retry next cycle.
                    message_id = (await self.say(uid, value, reply_markup=keyboard(job))).message_id
        else:
            message_id = (await self.say(uid, value, reply_markup=keyboard(job))).message_id
        await self.api.call('PUT', f"/jobs/{job['job_code']}/notification", uid,
            json={'message_id': message_id, 'text': value, 'terminal': job['status'] in TERMINAL})

    async def notifications(self):
        while True:
            users, after = [], None
            try:
                while True:
                    page = await self.api.call('GET', '/users', params={'after': after} if after else {})
                    users.extend(page['users'])
                    after = page['next']
                    if after is None:
                        break
            except APIError:
                logging.warning('Backend пользователей недоступен')
            for uid in users:
                cursor = None
                try:
                    while True:
                        params = {'pending': 'true', 'limit': 100}
                        if cursor:
                            params['before'] = cursor
                        result = await self.api.call('GET', '/jobs', uid, params=params)
                        for job in result['jobs']:
                            try:
                                await self.update(uid, job)
                            except (TelegramNetworkError, TelegramForbiddenError, TelegramBadRequest):
                                logging.warning('Не удалось обновить уведомление; будет повторено')
                        cursor = result.get('next')
                        if not cursor:
                            break
                except (APIError, httpx.HTTPError):
                    logging.warning('Backend уведомлений недоступен')
            await asyncio.sleep(max(5, int(os.getenv('TELEGRAM_POLL_SECONDS', '10'))))


async def main():
    api = API()
    bot = make_bot()
    app = App(api, bot)
    dispatcher = Dispatcher()
    dispatcher.include_router(app.router)
    owner = secrets.token_hex(16)
    notification_task = None
    lease_task = None
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, main_task.cancel)

    async def renew_lease():
        while True:
            await asyncio.sleep(20)
            try:
                await api.call('POST', '/runtime', json={'owner': owner})
            except APIError:
                logging.error('Не удалось продлить аренду; бот останавливается')
                main_task.cancel()
                return
    try:
        state = await api.call('POST', '/runtime', json={'owner': owner})
        offset = state['offset']
        with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
            await bot.set_my_commands([
                BotCommand(command='start', description='Начать работу'),
                BotCommand(command='status', description='Последнее задание'),
                BotCommand(command='myjobs', description='Последние задания'),
                BotCommand(command='help', description='Справка, лимиты и сроки хранения'),
            ], request_timeout=20)
        lease_task = asyncio.create_task(renew_lease())
        notification_task = asyncio.create_task(app.notifications())
        while True:
            try:
                # Lease is renewed before long poll; duplicate instances fail closed.
                await api.call('POST', '/runtime', json={'owner': owner, 'offset': offset})
                updates = await bot.get_updates(offset=offset, timeout=25,
                                               allowed_updates=['message', 'callback_query'], request_timeout=35)
                for update in updates:
                    try:
                        await dispatcher.feed_update(bot, update)
                    except TelegramForbiddenError:
                        logging.warning('Пользователь заблокировал бота; update пропущен')
                    offset = update.update_id + 1
                    await api.call('POST', '/runtime', json={'owner': owner, 'offset': offset})
            except APIError as exc:
                if exc.status == 409:
                    raise RuntimeError('Another bot instance holds the lease') from None
                logging.warning('Backend недоступен; повтор через 5 секунд')
                await asyncio.sleep(5)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except (TelegramNetworkError, TelegramBadRequest, TelegramForbiddenError, httpx.HTTPError):
                logging.warning('Telegram временно недоступен; повтор через 5 секунд')
                await asyncio.sleep(5)
    finally:
        if lease_task:
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task
        if notification_task:
            notification_task.cancel()
            with suppress(asyncio.CancelledError):
                await notification_task
        with suppress(APIError, httpx.HTTPError):
            await api.call('DELETE', f'/runtime/{owner}')
        await bot.session.close()
        await api.client.aclose()


if __name__ == '__main__':
    # Do not log Telegram URLs (contain token), update bodies or backend responses.
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')
    logging.getLogger('aiogram').setLevel(logging.CRITICAL)
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception:
        logging.error('Бот остановлен. Проверьте конфигурацию и доступность сервисов.')
        raise SystemExit(1)
