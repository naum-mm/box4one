#!/usr/bin/env python3
import argparse, math, os, re, secrets
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
DEFAULTS=dict(DOMAIN='example.com',MARIADB_DATABASE='ytdlp',MARIADB_USER='ytdlp',MARIADB_ROOT_PASSWORD='',MARIADB_PASSWORD='',WORKER_API_TOKEN='',DOWNLOAD_ACCESS_WORD='',QUEUE_ACCESS_WORD='',JOB_TTL_MINUTES='30',POLL_INTERVAL='5',VIDEO_PLAYLIST_LIMIT='50',AUDIO_PLAYLIST_LIMIT='100',MIN_FREE_GB='2',MAX_JOB_GB='3',DISK_CHECK_SECONDS='1',RETRY_MAX_SECONDS='60')
def read():
    result={}
    for line in (ROOT/'.env').read_text().splitlines():
        if not line.strip() or line.startswith('#'):continue
        k,v=line.split('=',1)
        if k not in DEFAULTS:raise ValueError('Unknown variable: '+k)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',v):raise ValueError('Only letters, digits, _, dot and hyphen allowed: '+k)
        result[k]=v
    if set(result)!=set(DEFAULTS):raise ValueError('Missing variables in .env')
    domain=result['DOMAIN']
    if len(domain)>253 or '.' not in domain or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',s) for s in domain.split('.')):raise ValueError('Invalid DOMAIN')
    for k in ['MARIADB_DATABASE','MARIADB_USER']:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',result[k]):raise ValueError('Invalid '+k)
    for k in list(DEFAULTS)[8:]:
        n=float(result[k])
        if not math.isfinite(n) or n<=0:raise ValueError('Must be positive: '+k)
        if k in ['JOB_TTL_MINUTES','POLL_INTERVAL','VIDEO_PLAYLIST_LIMIT','AUDIO_PLAYLIST_LIMIT'] and not result[k].isdigit():raise ValueError('Must be integer: '+k)
    if result['DOWNLOAD_ACCESS_WORD']==result['QUEUE_ACCESS_WORD']:raise ValueError('Access words must differ')
    return result
p=argparse.ArgumentParser();p.add_argument('mode',choices=['init','http','https','domain']);p.add_argument('--domain');a=p.parse_args()
os.umask(0o077)
if a.mode=='init' and not (ROOT/'.env').exists():
    if not a.domain:p.error('--domain is required')
    values=DEFAULTS.copy();values['DOMAIN']=a.domain.lower()
    for k in ['MARIADB_ROOT_PASSWORD','MARIADB_PASSWORD','WORKER_API_TOKEN','DOWNLOAD_ACCESS_WORD','QUEUE_ACCESS_WORD']:values[k]=secrets.token_hex(24)
    (ROOT/'.env').write_text(''.join(k+'='+v+'\n' for k,v in values.items()))
values=read()
if a.domain and a.domain.lower()!=values['DOMAIN']:raise SystemExit('Domain differs from existing .env; not overwriting')
if a.mode=='domain':print(values['DOMAIN'])
if a.mode in ['http','https'] or (a.mode=='init' and not (ROOT/'nginx/nginx.conf').exists()):
    mode=a.mode if a.mode!='init' else 'http'
    (ROOT/'nginx/nginx.conf').write_text((ROOT/f'nginx/{mode}.conf.template').read_text().replace('__DOMAIN__',values['DOMAIN']))
    (ROOT/'nginx/nginx.conf').chmod(0o644)
(ROOT/'.env').chmod(0o600)
