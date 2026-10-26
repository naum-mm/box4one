#!/usr/bin/env python3
"""Explicit migration only; never log out or change transports on bot startup."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def compose(*args, **kwargs):
    return subprocess.run(['docker', 'compose', *args], cwd=ROOT, check=True, **kwargs)


def settings():
    return dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines()
                if '=' in line and not line.lstrip().startswith('#'))


def write_settings(changes):
    path = ROOT / '.env'
    lines = path.read_text().splitlines()
    keys = set()
    result = []
    for line in lines:
        key = line.split('=', 1)[0]
        if key in changes:
            result.append(key + '=' + changes[key]); keys.add(key)
        else:
            result.append(line)
    result.extend(key + '=' + value for key, value in changes.items() if key not in keys)
    with tempfile.NamedTemporaryFile('w', dir=ROOT, prefix='.env.telegram-switch.', delete=False) as output:
        output.write('\n'.join(result) + '\n')
        temp = Path(output.name)
    temp.replace(path)


def call(endpoint, method):
    # Token is read inside the short-lived container, never placed in argv/output.
    code = '''import os,sys,httpx
try:
 r=httpx.post(sys.argv[1]+'/bot'+os.environ['TELEGRAM_BOT_TOKEN']+'/'+sys.argv[2],timeout=30,trust_env=False)
 data=r.json()
 if not data.get('ok') or (sys.argv[2]=='getWebhookInfo' and data['result'].get('url')): raise ValueError()
 print('Telegram API check OK')
except Exception: raise SystemExit('Telegram API call failed; no response body logged')
'''
    compose('run', '--rm', '--no-deps', 'telegram-bot', 'python', '-c', code, endpoint, method)


def reset_runtime_cursor(previous, current):
    """Offsets belong to one server queue; only run with the poller stopped."""
    running = compose('ps', '--status', 'running', '--services', capture_output=True, text=True).stdout.split()
    if 'telegram-bot' in running:
        raise RuntimeError('Stop the polling process before changing queue offsets')
    audit = ROOT / '.telegram-transport-cursors.json'
    entries = json.loads(audit.read_text()) if audit.exists() else []
    if not isinstance(entries, list):
        raise ValueError('Invalid transport cursor audit')
    code = """from datetime import datetime
from app.database import SessionLocal
from app.telegram_models import TelegramRuntime
with SessionLocal.begin() as db:
 state=db.get(TelegramRuntime,1,with_for_update=True)
 assert state is not None
 previous=state.offset
 state.offset=0
 state.owner=''
 state.lease_until=datetime.utcnow()
print(previous)
"""
    result = compose('run', '--rm', '--no-deps', 'backend', 'python', '-c', code,
                     capture_output=True, text=True)
    old_offset = int(result.stdout.strip().splitlines()[-1])
    entries.append({'previous_transport': previous, 'previous_offset': old_offset,
                    'transport': current, 'offset': 0,
                    'at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    with tempfile.NamedTemporaryFile('w', dir=ROOT, prefix='.telegram-cursors.', delete=False) as output:
        json.dump(entries, output, indent=2)
        temp = Path(output.name)
    temp.replace(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['local', 'cloud'])
    args = parser.parse_args()
    env = settings()
    if args.mode == 'local':
        import re
        if not env.get('TELEGRAM_API_ID', '').isdigit() or int(env['TELEGRAM_API_ID']) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', env.get('TELEGRAM_API_HASH', '')):
            raise SystemExit('Set your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first')
        if env.get('TELEGRAM_LOCAL_API_ENABLED', 'false') == 'true':
            raise SystemExit('Already configured for local mode; use compose up to restart')
        data = ROOT / 'telegram-api-data'
        data.mkdir(mode=0o700, exist_ok=True)
        os.chown(data, 65532, 65532)
        compose('--profile', 'telegram-local', 'up', '-d', '--no-deps', '--wait', 'telegram-api')
        # Verify HTTP startup before touching the poller; getMe verifies credentials after logOut.
        compose('exec', '-T', 'telegram-api', 'python', '/app/health.py')
        compose('stop', '--timeout', '60', 'telegram-bot')
        try:
            call('https://api.telegram.org', 'getWebhookInfo')
            call('https://api.telegram.org', 'logOut')
        except Exception:
            compose('start', 'telegram-bot')
            raise
        write_settings({'TELEGRAM_LOCAL_API_ENABLED': 'true',
                        'TELEGRAM_LOCAL_API_URL': 'http://telegram-api:8081',
                        'COMPOSE_PROFILES': 'telegram,telegram-local'})
        # Never drop_pending_updates. Archive the old server's cursor before polling the new queue.
        call('http://telegram-api:8081', 'getMe')
        reset_runtime_cursor('cloud', 'local')
        compose('up', '-d', '--no-deps', 'telegram-bot')
        print('Local mode enabled. Cloud login is unavailable for 10 minutes after logOut.')
    else:
        if env.get('TELEGRAM_LOCAL_API_ENABLED', 'false') != 'true':
            raise SystemExit('Already configured for cloud mode')
        compose('stop', '--timeout', '60', 'telegram-bot')
        # Must succeed before cloud polling is enabled; never run competing sessions.
        call('http://telegram-api:8081', 'logOut')
        compose('--profile', 'telegram-local', 'stop', 'telegram-api')
        print('Waiting 10 minutes before cloud login. Keep this command running.', flush=True)
        time.sleep(600)
        write_settings({'TELEGRAM_LOCAL_API_ENABLED': 'false', 'COMPOSE_PROFILES': 'telegram'})
        call('https://api.telegram.org', 'getMe')
        reset_runtime_cursor('local', 'cloud')
        compose('up', '-d', '--no-deps', 'telegram-bot')
        print('Cloud mode enabled; uploads are limited to 49 MB.')


if __name__ == '__main__':
    main()
