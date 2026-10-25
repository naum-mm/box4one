"""Lifecycle checks use temporary data and mocked Docker, never the host stack."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('box4one_manage', SOURCE / 'scripts/manage.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        shutil.copytree(SOURCE / 'nginx', self.root / 'nginx')
        (self.root / 'scripts').mkdir()
        shutil.copy2(SOURCE / 'scripts/configure.py', self.root / 'scripts/configure.py')
        subprocess.run(['python3', str(self.root / 'scripts/configure.py'), 'init',
                        '--domain', 'video.example.com'], check=True)
        (self.root / '.box4one-install').write_text(str(self.root) + '\n')
        (self.root / 'compose.yaml').write_text('services: {}\n')
        (self.root / 'jobs').mkdir()
        (self.root / 'jobs/result.mp4').write_bytes(b'synthetic result')
        (self.root / 'mariadb/data').mkdir(parents=True)
        (self.root / 'mariadb/data/history').write_bytes(b'live database sentinel')
        self.backups = self.base / 'backups'
        self.env = (self.root / '.env').read_bytes()
        self.nginx = (self.root / 'nginx/nginx.conf').read_bytes()
        self.sql = b'CREATE DATABASE ytdlp;\n-- synthetic web and telegram history\n'
        self.calls = []
        self.imported = []

    def compose(self, root, *args, **kwargs):
        self.calls.append(args)
        if args[:1] == ('exec',) and args[-1] == manager.DUMP:
            kwargs['stdout'].write(self.sql)
        if args[:1] == ('exec',) and args[-1] == manager.IMPORT:
            self.imported.append(kwargs['stdin'].read())
        return subprocess.CompletedProcess(args, 0, stdout='')

    def take_backup(self, files=False):
        with patch.object(manager, 'compose', side_effect=self.compose), \
             patch.object(manager, 'running_services', return_value=['mariadb']):
            return manager.backup(self.root, self.backups, files)

    def test_backup_preserves_secrets_history_and_optional_results(self):
        archive = self.take_backup()
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backups.stat().st_mode & 0o777, 0o700)
        with tarfile.open(archive) as data:
            self.assertEqual(data.extractfile('.env').read(), self.env)
            self.assertEqual(data.extractfile('database.sql').read(), self.sql)
            self.assertNotIn('jobs/result.mp4', data.getnames())
        with tarfile.open(self.take_backup(files=True)) as data:
            self.assertEqual(data.extractfile('jobs/result.mp4').read(), b'synthetic result')

    def test_dump_failure_leaves_no_success_archive(self):
        with patch.object(manager, 'compose', side_effect=RuntimeError('dump failed')), \
             patch.object(manager, 'running_services', return_value=['mariadb']):
            with self.assertRaises(RuntimeError):
                manager.backup(self.root, self.backups)
        self.assertFalse(list(self.backups.glob('*.tar.gz')))
        self.assertEqual((self.root / '.env').read_bytes(), self.env)

    def test_stopped_database_is_started_only_for_backup_then_stopped(self):
        with patch.object(manager, 'compose', side_effect=self.compose), \
             patch.object(manager, 'running_services', return_value=[]):
            manager.backup(self.root, self.backups)
        self.assertEqual(self.calls[0][:2], ('up', '-d'))
        self.assertEqual(self.calls[-1], ('stop', 'mariadb'))

    def test_no_backup_inside_installation(self):
        with self.assertRaises(ValueError):
            manager.backup(self.root, self.root / 'backups')

    def test_invalid_marker_rejects_operation_before_docker(self):
        (self.root / '.box4one-install').unlink()
        with patch.object(manager.subprocess, 'run') as command:
            with self.assertRaises(ValueError):
                manager.validate_root(self.root)
            command.assert_not_called()

    def test_keep_mode_preserves_all_state(self):
        with patch.object(manager, 'validate_root'), \
             patch.object(manager, 'running_services', return_value=[]), \
             patch.object(manager, 'compose', side_effect=self.compose):
            manager.uninstall(self.root, self.backups)
        self.assertEqual((self.root / '.env').read_bytes(), self.env)
        self.assertEqual((self.root / 'jobs/result.mp4').read_bytes(), b'synthetic result')
        self.assertTrue((self.root / 'mariadb/data/history').exists())
        self.assertEqual(self.calls, [('--profile', 'telegram', 'down')])

    def test_failed_backup_cancels_purge_and_resumes_old_services(self):
        with patch.object(manager, 'validate_root'), \
             patch.object(manager, 'running_services', return_value=['mariadb', 'worker', 'backend']), \
             patch.object(manager, 'compose', side_effect=self.compose), \
             patch.object(manager, 'backup', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                manager.uninstall(self.root, self.backups, purge=True, yes=True)
        self.assertTrue(self.root.exists())
        self.assertEqual(self.calls[-1], ('start', 'worker', 'backend'))
        self.assertFalse(any('down' in call for call in self.calls))

    def test_purge_has_restorable_archive_before_directory_is_deleted(self):
        with patch.object(manager, 'validate_root'), \
             patch.object(manager, 'running_services', return_value=['mariadb']), \
             patch.object(manager, 'compose', side_effect=self.compose):
            manager.uninstall(self.root, self.backups, purge=True, include_files=True, yes=True)
        self.assertFalse(self.root.exists())
        with tarfile.open(next(self.backups.glob('*.tar.gz'))) as data:
            self.assertEqual(data.extractfile('.env').read(), self.env)
            self.assertEqual(data.extractfile('database.sql').read(), self.sql)
            self.assertIn('jobs/result.mp4', data.getnames())

    def test_cancelled_purge_does_not_stop_or_dump(self):
        with patch.object(manager, 'validate_root'), patch('builtins.input', return_value='NO'), \
             patch.object(manager, 'compose') as compose:
            with self.assertRaises(ValueError):
                manager.uninstall(self.root, self.backups, purge=True)
            compose.assert_not_called()
        self.assertTrue(self.root.exists())

    def test_busy_worker_is_resumed_and_update_does_not_change_files(self):
        with patch.object(manager, 'validate_root'), \
             patch.object(manager, 'running_services', return_value=['mariadb', 'worker']), \
             patch.object(manager, 'compose', side_effect=self.compose), \
             patch.object(manager, 'active_jobs', return_value=1), \
             patch.object(manager, 'backup') as backup:
            with self.assertRaises(ValueError):
                manager.update(self.root, SOURCE, self.backups)
            backup.assert_not_called()
        self.assertEqual(self.calls, [('pause', 'worker'), ('unpause', 'worker')])
        self.assertEqual((self.root / '.env').read_bytes(), self.env)

    def test_update_preserves_local_config_database_and_results(self):
        with patch.object(manager, 'validate_root'), \
             patch.object(manager, 'running_services', return_value=['mariadb', 'worker', 'backend']), \
             patch.object(manager, 'compose', side_effect=self.compose), \
             patch.object(manager, 'active_jobs', return_value=0), \
             patch.object(manager, 'start') as start:
            manager.update(self.root, SOURCE, self.backups)
            start.assert_called_once_with(self.root)
        self.assertEqual((self.root / '.env').read_bytes(), self.env)
        self.assertEqual((self.root / 'nginx/nginx.conf').read_bytes(), self.nginx)
        self.assertEqual((self.root / 'mariadb/data/history').read_bytes(), b'live database sentinel')
        self.assertEqual((self.root / 'jobs/result.mp4').read_bytes(), b'synthetic result')
        self.assertTrue(list(self.backups.glob('*.tar.gz')))
        self.assertLess(self.calls.index(('pause', 'worker')),
                        self.calls.index(('stop', '--timeout', '0', 'worker')))

    def test_restore_imports_history_before_start_preserving_config_and_files(self):
        archive = self.take_backup(files=True)
        target = self.base / 'restored'
        original_run = manager.run
        def run(command, root, **kwargs):
            if command[:2] == ['bash', './install.sh']:
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, root, **kwargs)
        def started(root):
            self.assertEqual(self.imported, [self.sql])
        with patch.object(manager, 'compose', side_effect=self.compose), \
             patch.object(manager, 'run', side_effect=run), \
             patch.object(manager, 'start', side_effect=started):
            manager.restore(target, SOURCE, archive)
        self.assertEqual((target / '.env').read_bytes(), self.env)
        self.assertEqual((target / 'nginx/nginx.conf').read_bytes(), self.nginx)
        self.assertEqual((target / 'jobs/result.mp4').read_bytes(), b'synthetic result')
        self.assertEqual((target / '.box4one-install').read_text().strip(), str(target))

    def test_restore_never_overwrites_existing_installation(self):
        with self.assertRaises(ValueError):
            manager.restore(self.root, SOURCE, self.base / 'missing.tar.gz')
        self.assertEqual((self.root / '.env').read_bytes(), self.env)

    def test_restore_rejects_path_traversal_and_links_before_installing(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('jobs/link', tarfile.SYMTYPE)):
            archive = self.base / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as data:
                item = tarfile.TarInfo(name)
                item.type = kind
                if kind == tarfile.SYMTYPE:
                    item.linkname = '/etc/passwd'
                data.addfile(item, io.BytesIO(b''))
            with self.assertRaises(ValueError):
                manager.restore(self.base / 'absent', SOURCE, archive)
            self.assertFalse((self.base / 'absent').exists())


if __name__ == '__main__':
    unittest.main()
