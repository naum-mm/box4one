"""Run locally: python3 -m unittest discover -s tests -v."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        shutil.copytree(SOURCE / 'nginx', self.root / 'nginx')
        (self.root / 'scripts').mkdir()
        shutil.copy(SOURCE / 'scripts/configure.py', self.root / 'scripts/configure.py')

    def configure(self, *args):
        return subprocess.run(['python3', str(self.root / 'scripts/configure.py'), *args],
                              text=True, capture_output=True)

    def test_repeated_configuration_preserves_credentials(self):
        self.assertEqual(self.configure('init', '--domain', 'video.example.com').returncode, 0)
        original = (self.root / '.env').read_bytes()
        self.assertEqual(self.configure('init', '--domain', 'video.example.com').returncode, 0)
        self.assertEqual((self.root / '.env').read_bytes(), original)
        self.assertEqual((self.root / '.env').stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(self.configure('init', '--domain', 'other.example.com').returncode, 0)
        self.assertEqual((self.root / '.env').read_bytes(), original)

    def test_https_generation_and_install_rerun_preserves_https(self):
        self.configure('init', '--domain', 'video.example.com')
        self.assertEqual(self.configure('https').returncode, 0)
        config = (self.root / 'nginx/nginx.conf').read_text()
        self.assertIn('live/video.example.com/fullchain.pem', config)
        self.assertIn('listen 443 ssl;', config)
        self.assertNotIn('__DOMAIN__', config)
        self.configure('init', '--domain', 'video.example.com')
        self.assertEqual((self.root / 'nginx/nginx.conf').read_text(), config)

    def test_single_env_contains_settings_and_rejects_invalid_history(self):
        self.assertEqual(self.configure('init', '--domain', 'video.example.com').returncode, 0)
        path = self.root / '.env'
        original = path.read_text()
        for key in ('TELEGRAM_HISTORY_LIMIT=10', 'MAX_JOB_GB=2', 'TELEGRAM_PUBLIC_URL=',
                    'TELEGRAM_UPLOAD_LIMIT_BYTES=49000000', 'COMPOSE_PROFILES='):
            self.assertIn(key, original)
        self.assertEqual(self.configure('check').returncode, 0)
        path.write_text(original.replace('TELEGRAM_HISTORY_LIMIT=10', 'TELEGRAM_HISTORY_LIMIT=0'))
        self.assertNotEqual(self.configure('check').returncode, 0)
        path.write_text(original + 'TELEGRAM_HISTORY_LIMIT=10\n')
        self.assertNotEqual(self.configure('check').returncode, 0)

    @unittest.skipUnless(os.geteuid() == 0, 'Uninstaller requires root; tested only against mocked Docker and temporary data')
    def test_uninstall_requires_marker_and_explicit_purge(self):
        shutil.copy(SOURCE / 'uninstall.sh', self.root / 'uninstall.sh')
        (self.root / 'compose.yaml').write_text('services: {}\n')
        data = self.root / 'jobs'; data.mkdir()
        (data / 'keep.txt').write_text('sample download')
        mockbin = Path(self.temp.name) / 'bin'; mockbin.mkdir()
        docker = mockbin / 'docker'
        docker.write_text('#!/bin/sh\nif [ "$1" = inspect ]; then exit 1; fi\nexit 0\n')
        docker.chmod(0o755)
        env = dict(os.environ, PATH=str(mockbin) + ':' + os.environ['PATH'])
        def invoke(*args, answer=''):
            return subprocess.run(['bash', str(self.root / 'uninstall.sh'), *args],
                                  env=env, input=answer, text=True, capture_output=True)
        self.assertNotEqual(invoke('--purge', '--yes').returncode, 0)
        self.assertTrue(data.exists())
        (self.root / '.box4one-install').write_text(str(self.root)+'\n')
        self.assertNotEqual(invoke('--purge', answer='NO\n').returncode, 0)
        self.assertTrue(data.exists())
        self.assertEqual(invoke().returncode, 0)
        self.assertTrue(data.exists())
        self.assertEqual(invoke('--purge', '--yes').returncode, 0)
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
