#!/usr/bin/env python3
"""Read-only administration helpers for ytdlp-web."""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ('mariadb', 'backend', 'nginx', 'worker', 'cleanup', 'download-proxy')
COMPOSE = ['docker', 'compose', '--project-directory', str(ROOT), '-f', str(ROOT / 'compose.yaml')]


def run(args, capture=False):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def db_report(mode, code='', limit=20):
    # Runs inside backend using its existing environment; credentials never enter argv.
    program = '''
import json,sys
from sqlalchemy import text
from app.database import engine
try:
    with engine.connect() as conn:
        mode,code,limit=sys.argv[1],sys.argv[2],int(sys.argv[3])
        if mode == 'counts':
            rows=conn.execute(text('SELECT status, COUNT(*) AS count FROM jobs GROUP BY status')).mappings().all()
        elif code:
            rows=conn.execute(text('SELECT job_code,status,media_type,quality,created_at,started_at,completed_at,expires_at,total_items,completed_items,error_message FROM jobs WHERE job_code=:code'), {'code':code}).mappings().all()
        else:
            rows=conn.execute(text("SELECT job_code,status,created_at,error_message FROM jobs WHERE status='failed' OR error_message IS NOT NULL ORDER BY id DESC LIMIT :n"), {'n':limit}).mappings().all()
        print(json.dumps([dict(r) for r in rows],ensure_ascii=False,default=str))
except Exception as exc:
    print('Database query failed: '+type(exc).__name__,file=sys.stderr)
    sys.exit(1)
'''
    result = run(COMPOSE + ['exec', '-T', 'backend', 'python', '-c', program, mode, code, str(limit)], True)
    if result.returncode:
        print('Не удалось прочитать БД через backend. Проверьте контейнеры backend и mariadb.', file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print('Неожиданный ответ БД.', file=sys.stderr)
        return None


def diagnose(_):
    bad = False
    print('Проект:', ROOT, flush=True)
    total, used, free = shutil.disk_usage(ROOT)
    print(f'Диск: свободно {free / 2**30:.2f} GiB из {total / 2**30:.2f} GiB', flush=True)
    run(['free', '-h'])
    if run(COMPOSE + ['ps', '-a']).returncode:
        return 1
    optional = run(COMPOSE + ['--profile', 'telegram', 'ps', '-a', '-q', 'telegram-bot'], True)
    enabled = bool(optional.stdout.strip())
    print('Telegram: ' + ('включён' if enabled else 'отключён (необязательный компонент)'))
    for service in SERVICES + (('telegram-bot',) if enabled else ()):
        ids = run(COMPOSE + ['ps', '-a', '-q', service], True)
        cid = ids.stdout.strip()
        if ids.returncode or not cid:
            print(service + ': контейнер отсутствует', flush=True)
            bad = True
            continue
        state = run(['docker', 'inspect', '--format', '{{json .State}}', cid], True)
        if state.returncode:
            print(service + ': состояние недоступно', flush=True)
            bad = True
            continue
        data = json.loads(state.stdout)
        health = data.get('Health', {}).get('Status', 'нет healthcheck')
        print(f'{service}: {data["Status"]}, health={health}, OOMKilled={data.get("OOMKilled")}, exit={data.get("ExitCode")}', flush=True)
        bad |= data['Status'] != 'running' or health in ('unhealthy', 'starting') or data.get('OOMKilled', False)
    print('\nВнутренний HTTP API backend:', flush=True)
    probe = "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=10); print(r.status,r.read().decode())"
    bad |= run(COMPOSE + ['exec', '-T', 'backend', 'python', '-c', probe]).returncode != 0
    print('\nПроверка Nginx:', flush=True)
    bad |= run(COMPOSE + ['exec', '-T', 'nginx', 'nginx', '-t']).returncode != 0
    print('\nКоличество заданий по статусам (БД):', flush=True)
    rows = db_report('counts')
    if rows is None:
        bad = True
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    print('\nЕсть проблемы.' if bad else '\nБазовые проверки пройдены.')
    return int(bad)


def logs(args):
    cmd = COMPOSE + ['logs', '--no-color', '--timestamps', '--tail', str(args.tail), '--since', args.since]
    if args.follow:
        cmd.append('--follow')
    if args.service != 'all':
        cmd.append(args.service)
    return run(cmd).returncode


def errors(args):
    code = args.code.lower() if args.code else ''
    if code and not re.fullmatch('[a-z0-9]{10}', code):
        print('Код задания должен содержать 10 латинских букв/цифр.', file=sys.stderr)
        return 2
    rows = db_report('errors', code, args.limit)
    failed = rows is None
    print('Данные БД (время UTC):')
    if rows is not None:
        print(json.dumps(rows, ensure_ascii=False, indent=2) if rows else 'Совпадений нет.')
    if not code:
        print('\nДля подробностей: ./scripts/queue-errors.sh КОД_ЗАДАНИЯ')
        return int(failed)
    print(f'\nЛоги: последние {args.tail} строк каждого сервиса за {args.since}.')
    print('Контекст после совпадения может содержать соседние события; это не обязательно ошибки задания.')
    matched = False
    for service in ('worker', 'backend', 'cleanup', 'nginx'):
        result = run(COMPOSE + ['logs', '--no-color', '--timestamps', '--since', args.since,
                                '--tail', str(args.tail), service], True)
        if result.returncode:
            print(f'{service}: логи недоступны.', file=sys.stderr)
            failed = True
            continue
        lines = result.stdout.splitlines()
        indexes = set()
        for i, line in enumerate(lines):
            if re.search(r'(?<![a-z0-9])' + re.escape(code) + r'(?![a-z0-9])', line, re.I):
                indexes.update(range(max(0, i-2), min(len(lines), i+41)))
        if indexes:
            matched = True
            print('\n--- ' + service + ' ---')
            previous = -2
            for i in sorted(indexes):
                if i != previous + 1:
                    print('...')
                print(lines[i])
                previous = i
    if not matched:
        print('Совпадений в доступных логах нет. Увеличьте --since и --tail; после пересоздания контейнера старые логи могут отсутствовать.')
    return int(failed)


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('Ожидается положительное число')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('diagnose', help='Проверить контейнеры, память, диск, HTTP, Nginx и БД')
    p.set_defaults(func=diagnose)
    p = sub.add_parser('logs', help='Показать логи сервисов')
    p.add_argument('service', nargs='?', choices=('all',) + SERVICES + ('telegram-bot',), default='all')
    p.add_argument('-f', '--follow', action='store_true')
    p.add_argument('--tail', type=positive, default=200)
    p.add_argument('--since', default='1h', help='Например 30m, 24h, 168h или timestamp')
    p.set_defaults(func=logs)
    p = sub.add_parser('errors', help='Последние ошибки или поиск по коду задания')
    p.add_argument('code', nargs='?')
    p.add_argument('--limit', type=positive, default=20)
    p.add_argument('--tail', type=positive, default=3000)
    p.add_argument('--since', default='24h')
    p.set_defaults(func=errors)
    args = parser.parse_args()
    if not shutil.which('docker'):
        parser.exit(1, 'Docker не найден.\n')
    if not (ROOT / 'compose.yaml').is_file():
        parser.exit(1, 'scripts/ должен находиться в папке проекта рядом с compose.yaml.\n')
    return args.func(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
