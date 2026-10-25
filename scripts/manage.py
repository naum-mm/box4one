#!/usr/bin/env python3
"""BOX4ONE lifecycle; secrets and durable state are never copied from Git."""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile

DEFAULT_ROOT = Path('/opt/ytdlp-web')
DEFAULT_BACKUPS = Path('/var/backups/box4one')
DUMP = ('MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -uroot '
        '--single-transaction --routines --events --triggers --databases "$MARIADB_DATABASE"')
IMPORT = 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot'


def run(command, root, **kwargs):
    return subprocess.run(command, cwd=root, check=True, **kwargs)


def compose(root, *args, **kwargs):
    return run(['docker', 'compose', *args], root, **kwargs)


def validate_root(root):
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('Installation path must be canonical, without symlinks')
    if len(root.parts) < 3 or root in (Path('/var/lib'), Path('/usr/local')):
        raise ValueError('Unsafe installation path')
    marker = root / '.box4one-install'
    if not marker.is_file() or marker.read_text().strip() != str(root):
        raise ValueError('Installation marker missing or path changed; nothing removed')
    if not (root / 'compose.yaml').is_file() or not (root / '.env').is_file():
        raise ValueError('Installation configuration missing')
    # Refuse to operate fixed-name containers belonging to another checkout.
    for name in ('mariadb', 'backend', 'nginx', 'worker', 'cleanup'):
        result = subprocess.run(['docker', 'inspect', '-f',
            '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
            'ytdlp-' + name], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() != str(root):
            raise ValueError('Container belongs to another installation')


def source_files(source):
    result = run(['git', 'ls-files', '-z'], source, capture_output=True).stdout
    names = [item.decode() for item in result.split(b'\0') if item]
    for name in names:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or (source / path).is_symlink():
            raise ValueError('Unsafe source file')
    return names


def copy_source(source, target):
    names = source_files(source)
    # Durable state and local Nginx configuration are not managed source files.
    forbidden = {'.env', '.box4one-install', 'nginx/nginx.conf'}
    for name in names:
        if name in forbidden or name.startswith(('jobs/', 'mariadb/data/', 'certbot/www/')):
            raise ValueError('Source contains durable data')
    previous = target / '.box4one-files.json'
    if previous.exists():
        for name in json.loads(previous.read_text()):
            p = Path(name)
            if p.is_absolute() or '..' in p.parts or name in forbidden:
                raise ValueError('Invalid installed file manifest')
            if name not in names and (target / p).is_file():
                (target / p).unlink()
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        destination = target / name
        # Never follow a local symlink when replacing managed code.
        if any(p.is_symlink() for p in (destination, *destination.parents) if p != target.parent):
            raise ValueError('Symlink in managed installation path')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    previous.write_text(json.dumps(names))


def active_jobs(root):
    query = ('MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot -N '
             '"$MARIADB_DATABASE" -e "SELECT COUNT(*) FROM jobs '
             "WHERE status IN ('downloading','processing')\"" )
    result = compose(root, 'exec', '-T', 'mariadb', 'sh', '-c', query,
                     capture_output=True, text=True)
    return int(result.stdout.strip())


def running_services(root):
    return compose(root, '--profile', 'telegram', 'ps', '--status', 'running', '--services',
                   capture_output=True, text=True).stdout.split()


@contextmanager
def database_running(root):
    was_running = 'mariadb' in running_services(root)
    if not was_running:
        compose(root, 'up', '-d', '--wait', '--wait-timeout', '240', 'mariadb')
    try:
        yield
    finally:
        if not was_running:
            compose(root, 'stop', 'mariadb')


def backup(root, directory, include_files=False):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.resolve().is_relative_to(root):
        raise ValueError('Backups must be outside the installation')
    directory.chmod(0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = directory / ('box4one-' + stamp + '.tar.gz')
    with database_running(root), tempfile.TemporaryDirectory(dir=directory) as scratch:
        scratch = Path(scratch)
        database = scratch / 'database.sql'
        with database.open('wb') as output:
            compose(root, 'exec', '-T', 'mariadb', 'sh', '-c', DUMP, stdout=output)
        if not database.stat().st_size:
            raise ValueError('Empty database dump; backup aborted')
        manifest = scratch / 'manifest.json'
        manifest.write_text(json.dumps({'format': 1, 'includes_files': include_files,
                                       'created_utc': stamp}))
        pending = scratch / 'archive.tar.gz'
        with tarfile.open(pending, 'w:gz') as archive:
            archive.add(database, arcname='database.sql')
            archive.add(manifest, arcname='manifest.json')
            for name in ('.env', 'nginx/nginx.conf'):
                archive.add(root / name, arcname=name)
            # Preserve deployed source as an additional manual rollback aid.
            files = root / '.box4one-files.json'
            names = (json.loads(files.read_text()) if files.exists() else
                     source_files(root) if (root / '.git').exists() else [])
            for name in names:
                if (root / name).is_file():
                    archive.add(root / name, arcname='source/' + name, recursive=False)
            if include_files and (root / 'jobs').exists():
                archive.add(root / 'jobs', arcname='jobs')
        # Read every member back to detect incomplete/corrupt compression before purge.
        with tarfile.open(pending, 'r:gz') as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as content:
                        while content.read(1024 * 1024):
                            pass
        pending.chmod(0o600)
        pending.replace(destination)
    print('Backup: ' + str(destination), flush=True)
    return destination


def start(root):
    compose(root, 'up', '-d', '--wait', '--wait-timeout', '240', 'mariadb')
    # One explicit, repeatable migration before starting any polling process.
    compose(root, 'run', '--rm', '--no-deps', 'backend', 'python', '-c',
            'from app.main import app; from app.telegram_migrate import migrate; migrate()')
    compose(root, 'up', '-d', '--wait', '--wait-timeout', '240')


def update(root, source, backups):
    validate_root(root)
    running = running_services(root)
    # Freeze claims before checking the DB; queued jobs stay queued. A busy worker
    # is resumed immediately. An idle worker can be stopped without a claim race.
    worker_paused = 'worker' in running
    if worker_paused:
        compose(root, 'pause', 'worker')
    try:
        with database_running(root):
            busy = active_jobs(root)
        if busy:
            raise ValueError('Active downloads exist; retry update after they finish')
    except Exception:
        if worker_paused:
            compose(root, 'unpause', 'worker')
        raise
    if worker_paused:
        compose(root, 'stop', '--timeout', '0', 'worker')
    paused = [name for name in running if name not in ('mariadb', 'download-proxy')]
    if paused:
        compose(root, 'stop', *paused)
    try:
        saved = backup(root, backups)
    except Exception:
        if paused:
            compose(root, 'start', *paused)
        raise
    # On a later failure keep the snapshot and stop; never overwrite newer history
    # with an automatic database rollback.
    print('Recovery snapshot: ' + str(saved), flush=True)
    copy_source(source, root)
    run(['python3', 'scripts/configure.py', 'init'], root)
    compose(root, 'config', '--quiet')
    compose(root, 'build')
    start(root)


def restore(root, source, filename):
    if root.exists():
        raise ValueError('Restore requires an absent installation directory')
    # Extract only data, using the safe data filter; never restore archived code.
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        with tarfile.open(filename, 'r:gz') as archive:
            members = []
            for member in archive:
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                    raise ValueError('Unsafe backup member')
                if member.name in {'.env', 'nginx/nginx.conf', 'database.sql', 'manifest.json'} or path.parts[:1] == ('jobs',):
                    if not (member.isfile() or member.isdir()):
                        raise ValueError('Unsupported backup member')
                    members.append(member)
            archive.extractall(scratch, members=members, filter='data')
        if json.loads((scratch / 'manifest.json').read_text())['format'] != 1:
            raise ValueError('Unsupported backup format')
        for name in ('.env', 'nginx/nginx.conf', 'database.sql'):
            if not (scratch / name).is_file():
                raise ValueError('Incomplete backup')
        # Validate the saved settings before creating an installation or containers.
        (scratch / 'scripts').mkdir()
        shutil.copy2(source / 'scripts/configure.py', scratch / 'scripts/configure.py')
        run(['python3', 'scripts/configure.py', 'check'], scratch)
        run(['bash', './install.sh', '--prepare-only'], source)
        copy_source(source, root)
        for name in ('.env', 'nginx/nginx.conf'):
            shutil.copy2(scratch / name, root / name)
        (root / '.env').chmod(0o600)
        if (scratch / 'jobs').exists():
            shutil.copytree(scratch / 'jobs', root / 'jobs')
        (root / '.box4one-install').write_text(str(root) + '\n')
        (root / '.box4one-install').chmod(0o600)
        run(['python3', 'scripts/configure.py', 'init'], root)
        compose(root, 'config', '--quiet')
        compose(root, 'build')
        compose(root, 'up', '-d', '--wait', '--wait-timeout', '240', 'mariadb')
        with (scratch / 'database.sql').open('rb') as database:
            compose(root, 'exec', '-T', 'mariadb', 'sh', '-c', IMPORT, stdin=database)
        start(root)
    print('Configuration and history restored. Certificates remain external; see docs/SETUP.md.')


def uninstall(root, backups, purge=False, include_files=False, yes=False):
    validate_root(root)
    if purge and not yes:
        if input('Delete the complete installation after backing up configuration/history? Type DELETE: ') != 'DELETE':
            raise ValueError('Cancelled; nothing removed')
    running = running_services(root)
    paused = [name for name in running if name not in ('mariadb', 'download-proxy')]
    if purge:
        if paused:
            compose(root, 'stop', *paused)
        try:
            backup(root, backups, include_files)
        except Exception:
            if paused:
                compose(root, 'start', *paused)
            raise
    compose(root, '--profile', 'telegram', 'down')
    hook = Path('/etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx')
    if purge and hook.is_file() and not hook.is_symlink():
        if 'cd ' + shlex.quote(str(root)) in hook.read_text().splitlines():
            hook.unlink()
    if purge:
        run(['rm', '-rf', '--one-file-system', '--', str(root)], root.parent)
        if root.exists():
            raise ValueError('Mounted files remain; backup retained, inspect installation directory')
        print('Installation removed; verified configuration/history backup retained outside it.')
    else:
        print('Stopped; configuration, database, downloads and renewal hook retained. Resume: docker compose up -d')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--target', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--backup-dir', type=Path, default=DEFAULT_BACKUPS)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('install').add_argument('domain')
    sub.add_parser('update')
    sub.add_parser('backup').add_argument('--include-files', action='store_true')
    sub.add_parser('restore').add_argument('archive', type=Path)
    delete = sub.add_parser('uninstall')
    delete.add_argument('--purge', action='store_true')
    delete.add_argument('--include-files', action='store_true')
    delete.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root')
    os.umask(0o077)
    root = args.target.absolute()
    # Serialize lifecycle commands, including those launched from a downloaded copy.
    with open('/run/lock/box4one-manage.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'install':
            if root.exists():
                raise ValueError('Installation directory exists; use update or restore into an absent directory')
            copy_source(args.source, root)
            run(['bash', './install.sh', args.domain], root)
        elif args.action == 'restore':
            restore(root, args.source, args.archive)
        else:
            validate_root(root)
            if args.action == 'update':
                update(root, args.source, args.backup_dir)
            elif args.action == 'backup':
                backup(root, args.backup_dir, args.include_files)
            else:
                uninstall(root, args.backup_dir, args.purge, args.include_files, args.yes)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        raise SystemExit('BOX4ONE: ' + str(error))
