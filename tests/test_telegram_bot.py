import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
from aiogram.methods import SendMessage, SendDocument
from aiogram.types import Message, Update
from telegram_bot.bot import API, APIError, App, BackendFile, STATUSES, text

UID = 10000000001
JOB = dict(job_code='abc123def4', status='queued', completed_items=0, total_items=0,
           files=[], message_id=5, last_text='', terminal_notified=False)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__api_method__ in {'sendMessage', 'editMessageText', 'sendDocument'}:
            return Message(message_id=5, date=0, chat={'id': UID, 'type': 'private'}, text='ok')
        return True

    async def stream_content(self, *args, **kwargs):
        yield b''


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = SimpleNamespace(call=AsyncMock(return_value={'jobs': []}))
        self.session = FakeSession()
        self.bot = Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789', session=self.session)
        self.app = App(self.api, self.bot, {UID})
        async def instant(uid, operation, **kwargs):
            return await operation(**kwargs)
        self.app.send = instant
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(self.app.router)

    async def event(self, value, uid=UID, chat_type='private'):
        update = Update(update_id=1, message={'message_id': 1, 'date': 0,
            'chat': {'id': uid, 'type': chat_type}, 'from': {'id': uid, 'is_bot': False, 'first_name': 'Test'}, 'text': value})
        await self.dispatcher.feed_update(self.bot, update)

    async def callback(self, value):
        update = Update(update_id=2, callback_query={'id': 'test', 'chat_instance': 'test',
            'from': {'id': UID, 'is_bot': False, 'first_name': 'Test'}, 'data': value,
            'message': {'message_id': 5, 'date': 0, 'chat': {'id': UID, 'type': 'private'}}})
        await self.dispatcher.feed_update(self.bot, update)

    async def test_start_help_status_myjobs(self):
        for command in ('/start', '/help', '/status', '/myjobs'):
            await self.event(command)
        self.assertEqual(len(self.session.calls), 4)
        self.assertIn('BOX4ONE', self.session.calls[0].text)
        self.assertIn('нет заданий', self.session.calls[-1].text)

    async def test_private_chats_and_allowlist(self):
        await self.event('/start', chat_type='group')
        self.assertEqual(self.session.calls, [])
        await self.event('/start', uid=99)
        self.assertEqual(self.session.calls[-1].text, 'Доступ запрещён.')
        self.api.call.assert_not_awaited()

    async def test_message_limit(self):
        await self.event('x' * 2049)
        self.assertIn('2048', self.session.calls[-1].text)
        self.api.call.assert_not_awaited()

    async def test_url_and_format_buttons(self):
        self.api.call.side_effect = [{'jobs': []}, {'draft_id': 'a' * 32}]
        await self.event('https://example.com/video')
        buttons = self.session.calls[-1].reply_markup.inline_keyboard
        self.assertEqual(len(buttons), 5)
        self.assertIn('a128', buttons[-1][0].callback_data)

    async def test_choice_calls_backend_and_edits_existing_message(self):
        self.api.call.side_effect = [dict(JOB), {'ok': True}]
        await self.callback('new:' + 'a' * 32 + ':v720')
        args = self.api.call.await_args_list[0]
        self.assertEqual(args.args[:2], ('POST', '/jobs'))
        self.assertEqual(args.kwargs['json']['message_id'], 5)
        self.assertEqual(self.session.calls[1].__api_method__, 'editMessageText')

    async def test_backend_errors_are_safe(self):
        self.api.call.side_effect = APIError(403)
        await self.event('/start')
        self.assertEqual(self.session.calls[-1].text, 'Доступ запрещён.')
        self.api.call.side_effect = APIError(503)
        with self.assertRaises(APIError):
            await self.event('/status')

    async def test_unchanged_progress_sends_nothing(self):
        job = dict(JOB); job['last_text'] = text(job)
        await self.app.update(UID, job)
        self.assertEqual(self.session.calls, [])

    async def test_terminal_statuses_persist_notification(self):
        for state in ('completed', 'failed', 'expired'):
            await self.app.update(UID, dict(JOB, status=state))
            self.assertTrue(self.api.call.await_args.kwargs['json']['terminal'])
            self.assertIn(state, STATUSES)

    async def test_small_upload_and_large_link(self):
        file = dict(file_id=1, filename='video.mp4', size_bytes=4)
        self.api.call.return_value = dict(JOB, status='completed', files=[file])
        await self.callback('file:abc123def4:1')
        self.assertIsInstance(self.session.calls[-1].document, BackendFile)
        self.api.call.side_effect = [dict(JOB, status='completed', files=[dict(file, size_bytes=50000000)]),
                                     {'url': 'https://example.com/signed'}]
        await self.callback('file:abc123def4:1')
        self.assertEqual(self.session.calls[-1].reply_markup.inline_keyboard[0][0].url, 'https://example.com/signed')

    async def test_rate_limit_retry(self):
        app = App(self.api, self.bot, {UID})
        method = SendMessage(chat_id=UID, text='test')
        operation = AsyncMock(side_effect=[TelegramRetryAfter(method=method, message='retry', retry_after=1), True])
        with patch('telegram_bot.bot.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertTrue(await app.send(UID, operation))
            self.assertEqual(operation.await_count, 2)
            self.assertTrue(any(c.args == (2,) for c in sleep.await_args_list))

    async def test_api_connection_loss_sanitized(self):
        api = API()
        await api.client.aclose()
        async def fail(request):
            raise httpx.ConnectError('sensitive backend details', request=request)
        api.client = httpx.AsyncClient(base_url='http://backend:8000', transport=httpx.MockTransport(fail))
        try:
            with self.assertRaises(APIError) as ctx:
                await api.call('GET', '/jobs', UID)
            self.assertEqual(ctx.exception.status, 503)
            self.assertNotIn('sensitive', ctx.exception.text)
        finally:
            await api.client.aclose()

    async def test_stream_upload_limit_enforced(self):
        api = SimpleNamespace(client=httpx.AsyncClient(base_url='http://backend:8000', transport=
            httpx.MockTransport(lambda r: httpx.Response(200, content=b'12345678'))))
        upload = BackendFile(api, UID, JOB['job_code'], {'filename': 'x', 'file_id': 1}, 4)
        try:
            with self.assertRaises(APIError):
                async for _ in upload.read(self.bot):
                    pass
        finally:
            await api.client.aclose()
