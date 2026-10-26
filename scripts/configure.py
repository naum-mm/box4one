#!/usr/bin/env python3
"""Validate and initialize the single project .env without replacing existing secrets."""
import argparse
import math
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = dict(
    DOMAIN='example.com', MARIADB_DATABASE='ytdlp', MARIADB_USER='ytdlp',
    MARIADB_ROOT_PASSWORD='', MARIADB_PASSWORD='', WORKER_API_TOKEN='',
    DOWNLOAD_ACCESS_WORD='', QUEUE_ACCESS_WORD='', JOB_TTL_MINUTES='30',
    POLL_INTERVAL='5', VIDEO_PLAYLIST_LIMIT='50', AUDIO_PLAYLIST_LIMIT='100',
    MIN_FREE_GB='2', MAX_JOB_GB='2', DISK_CHECK_SECONDS='1', RETRY_MAX_SECONDS='60',
    TELEGRAM_HISTORY_LIMIT='10', ARCHIVE_MAX_CONCURRENT='1', CLEANUP_INTERVAL_SECONDS='60',
)
OPTIONAL = dict(
    COMPOSE_PROFILES='', COMPOSE_PROJECT_NAME='ytdlp-web',
    TELEGRAM_BOT_TOKEN='', TELEGRAM_INTERNAL_TOKEN='', TELEGRAM_ALLOWED_USERS='',
    TELEGRAM_ACTIVATION_WORD='REDACTED_ACTIVATION_WORD', TELEGRAM_API_ID='', TELEGRAM_API_HASH='',
    TELEGRAM_LOCAL_API_ENABLED='false', TELEGRAM_LOCAL_API_URL='http://telegram-api:8081',
    TELEGRAM_LOCAL_UPLOAD_LIMIT_BYTES='2000000000', TELEGRAM_API_MEMORY='256m', TELEGRAM_API_SWAP='512m',
    TELEGRAM_PUBLIC_URL='', TELEGRAM_API_URL='http://backend:8000',
    TELEGRAM_MAX_ACTIVE_JOBS='2', TELEGRAM_POLL_SECONDS='10',
    TELEGRAM_UPLOAD_LIMIT_BYTES='49000000', TELEGRAM_LINK_TTL_SECONDS='600',
    TELEGRAM_DRAFT_TTL_MINUTES='60', TELEGRAM_MAX_DRAFTS_PER_HOUR='60',
    TELEGRAM_CHAT_INTERVAL_SECONDS='1.1', TELEGRAM_SEND_INTERVAL_SECONDS='0.06',
    DB_MEMORY='128m', DB_SWAP='256m', DB_BUFFER_POOL_SIZE='32M', DB_MAX_CONNECTIONS='30',
    BACKEND_MEMORY='128m', BACKEND_SWAP='256m', WORKER_MEMORY='384m', WORKER_SWAP='768m',
    TELEGRAM_MEMORY='192m', TELEGRAM_SWAP='384m', PROXY_MEMORY='64m', PROXY_SWAP='128m',
    SERVICE_CPU_LIMIT='0.5', LOG_MAX_SIZE='5m', LOG_MAX_FILES='2',
    INTERNAL_NETWORK_NAME='', EGRESS_NETWORK_NAME='', EGRESS_NETWORK_EXTERNAL='false',
)
SECRET_KEYS = ('MARIADB_ROOT_PASSWORD', 'MARIADB_PASSWORD', 'WORKER_API_TOKEN',
               'DOWNLOAD_ACCESS_WORD', 'QUEUE_ACCESS_WORD')
FLOAT_KEYS = {'MIN_FREE_GB', 'MAX_JOB_GB', 'DISK_CHECK_SECONDS', 'RETRY_MAX_SECONDS',
              'TELEGRAM_CHAT_INTERVAL_SECONDS', 'TELEGRAM_SEND_INTERVAL_SECONDS', 'SERVICE_CPU_LIMIT'}
INT_KEYS = {'JOB_TTL_MINUTES', 'POLL_INTERVAL', 'VIDEO_PLAYLIST_LIMIT', 'AUDIO_PLAYLIST_LIMIT',
            'TELEGRAM_HISTORY_LIMIT', 'ARCHIVE_MAX_CONCURRENT', 'CLEANUP_INTERVAL_SECONDS',
            'TELEGRAM_MAX_ACTIVE_JOBS', 'TELEGRAM_POLL_SECONDS', 'TELEGRAM_UPLOAD_LIMIT_BYTES',
            'TELEGRAM_LINK_TTL_SECONDS', 'TELEGRAM_DRAFT_TTL_MINUTES',
            'TELEGRAM_MAX_DRAFTS_PER_HOUR', 'DB_MAX_CONNECTIONS', 'LOG_MAX_FILES'}


def read():
    supplied = {}
    for line in (ROOT / '.env').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key not in DEFAULTS and key not in OPTIONAL:
            raise ValueError('Unknown variable: ' + key)
        if key in supplied:
            raise ValueError('Duplicate variable: ' + key)
        if key in {'TELEGRAM_PUBLIC_URL', 'TELEGRAM_API_URL', 'TELEGRAM_LOCAL_API_URL'}:
            parts = urlsplit(value)
            if value and (parts.scheme not in {'http', 'https'} or not parts.hostname
                          or parts.username or parts.password or parts.query or parts.fragment
                          or parts.path not in {'', '/'}):
                raise ValueError('Invalid URL: ' + key)
            if key == 'TELEGRAM_PUBLIC_URL' and value and parts.scheme != 'https':
                raise ValueError('Public links require HTTPS')
        else:
            pattern = (r'[0-9]+:[A-Za-z0-9_-]+' if key == 'TELEGRAM_BOT_TOKEN' else
                       r'[0-9]+(?:,[0-9]+)*' if key == 'TELEGRAM_ALLOWED_USERS' else
                       r'[A-Za-z0-9_.-]*')
            if value and not re.fullmatch(pattern, value):
                raise ValueError('Invalid ' + key)
        supplied[key] = value
    if any(not supplied.get(key) for key in (*SECRET_KEYS, 'DOMAIN', 'MARIADB_DATABASE', 'MARIADB_USER')):
        raise ValueError('Missing required variables in .env')
    result = DEFAULTS | OPTIONAL | supplied
    domain = result['DOMAIN']
    if len(domain) > 253 or '.' not in domain or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.')):
        raise ValueError('Invalid DOMAIN')
    for key in ('MARIADB_DATABASE', 'MARIADB_USER'):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', result[key]):
            raise ValueError('Invalid ' + key)
    for key in FLOAT_KEYS | INT_KEYS:
        value = result[key]
        number = float(value)
        if not math.isfinite(number) or number <= 0 or (key in INT_KEYS and not value.isdigit()):
            raise ValueError('Must be positive: ' + key)
    if not 1 <= int(result['TELEGRAM_HISTORY_LIMIT']) <= 100:
        raise ValueError('TELEGRAM_HISTORY_LIMIT must be between 1 and 100')
    if int(result['TELEGRAM_UPLOAD_LIMIT_BYTES']) > 49000000:
        raise ValueError('TELEGRAM_UPLOAD_LIMIT_BYTES must not exceed 49000000')
    for key in ('TELEGRAM_INTERNAL_TOKEN',):
        if result[key] and len(result[key]) < 32:
            raise ValueError('Internal token must contain at least 32 characters')
    for key in OPTIONAL:
        if key.endswith(('_MEMORY', '_SWAP')) or key in {'DB_BUFFER_POOL_SIZE', 'LOG_MAX_SIZE'}:
            if not re.fullmatch(r'[1-9][0-9]*[kKmMgG]?', result[key]):
                raise ValueError('Invalid size: ' + key)
    if result['COMPOSE_PROFILES'] not in {'', 'telegram', 'telegram,telegram-local'}:
        raise ValueError('COMPOSE_PROFILES must be empty, telegram or telegram,telegram-local')
    if result['EGRESS_NETWORK_EXTERNAL'] not in {'true', 'false'}:
        raise ValueError('EGRESS_NETWORK_EXTERNAL must be true or false')
    if 'telegram' in result['COMPOSE_PROFILES'].split(',') and any(not result[key] for key in (
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_INTERNAL_TOKEN', 'TELEGRAM_ACTIVATION_WORD')):
        raise ValueError('Telegram profile requires bot token, internal token and activation word')
    if result['TELEGRAM_LOCAL_API_ENABLED'] not in {'true', 'false'}:
        raise ValueError('TELEGRAM_LOCAL_API_ENABLED must be true or false')
    limit = result['TELEGRAM_LOCAL_UPLOAD_LIMIT_BYTES']
    if not limit.isdigit() or not 1 <= int(limit) <= 2000000000:
        raise ValueError('Local upload limit must be between 1 and 2000000000 bytes')
    if result['TELEGRAM_LOCAL_API_ENABLED'] == 'true':
        if result['COMPOSE_PROFILES'] != 'telegram,telegram-local':
            raise ValueError('Local API requires both Compose profiles')
        if not result['TELEGRAM_API_ID'].isdigit() or int(result['TELEGRAM_API_ID']) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', result['TELEGRAM_API_HASH']):
            raise ValueError('Local API requires your Telegram API ID and API hash')
        if result['TELEGRAM_LOCAL_API_URL'] != 'http://telegram-api:8081':
            raise ValueError('Local API must use the private telegram-api:8081 service')
    if result['DOWNLOAD_ACCESS_WORD'] == result['QUEUE_ACCESS_WORD']:
        raise ValueError('Access words must differ')
    return result, supplied


parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['init', 'http', 'https', 'domain', 'check'])
parser.add_argument('--domain')
args = parser.parse_args()
os.umask(0o077)
if args.mode == 'init' and not (ROOT / '.env').exists():
    if not args.domain:
        parser.error('--domain is required')
    values = DEFAULTS | OPTIONAL
    values['DOMAIN'] = args.domain.lower()
    for key in (*SECRET_KEYS, 'TELEGRAM_INTERNAL_TOKEN'):
        values[key] = secrets.token_hex(24)
    (ROOT / '.env').write_text(''.join(key + '=' + value + '\n' for key, value in values.items()))
values, supplied = read()
if args.domain and args.domain.lower() != values['DOMAIN']:
    raise SystemExit('Domain differs from existing .env; not overwriting')
if args.mode == 'init':
    missing = {key: value for key, value in values.items() if key not in supplied}
    if missing:
        with (ROOT / '.env').open('a') as output:
            output.write('\n' + ''.join(key + '=' + value + '\n' for key, value in missing.items()))
if args.mode == 'domain':
    print(values['DOMAIN'])
if args.mode in {'http', 'https'} or (args.mode == 'init' and not (ROOT / 'nginx/nginx.conf').exists()):
    mode = args.mode if args.mode != 'init' else 'http'
    (ROOT / 'nginx/nginx.conf').write_text(
        (ROOT / f'nginx/{mode}.conf.template').read_text().replace('__DOMAIN__', values['DOMAIN']))
    (ROOT / 'nginx/nginx.conf').chmod(0o644)
(ROOT / '.env').chmod(0o600)
