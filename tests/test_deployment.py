"""Run locally: python3 -m unittest discover -s tests -v."""
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

    def test_local_profiles_and_credentials_validate_together(self):
        self.assertEqual(self.configure('init', '--domain', 'video.example.com').returncode, 0)
        path = self.root / '.env'
        values = dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line)
        values.update(COMPOSE_PROFILES='telegram,telegram-local', TELEGRAM_LOCAL_API_ENABLED='true',
                      TELEGRAM_BOT_TOKEN='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789',
                      TELEGRAM_API_ID='12345', TELEGRAM_API_HASH='a'*32)
        path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()))
        self.assertEqual(self.configure('check').returncode, 0)
        path.write_text(path.read_text().replace('TELEGRAM_API_HASH='+'a'*32, 'TELEGRAM_API_HASH='))
        self.assertNotEqual(self.configure('check').returncode, 0)



if __name__ == '__main__':
    unittest.main()


class TelegramSwitchTests(unittest.TestCase):
    def test_switch_preserves_secrets_and_uses_private_atomic_file(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('telegram_switch', SOURCE / 'scripts/telegram_local.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / '.env'
            path.write_text('TELEGRAM_BOT_TOKEN=keep-this\nTELEGRAM_LOCAL_API_ENABLED=false\n# retained comment\n')
            with patch.object(module, 'ROOT', root):
                module.write_settings({'TELEGRAM_LOCAL_API_ENABLED': 'true', 'COMPOSE_PROFILES': 'telegram,telegram-local'})
                values = module.settings()
            self.assertEqual(values['TELEGRAM_BOT_TOKEN'], 'keep-this')
            self.assertEqual(values['TELEGRAM_LOCAL_API_ENABLED'], 'true')
            self.assertEqual(values['COMPOSE_PROFILES'], 'telegram,telegram-local')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn('# retained comment', path.read_text())
            self.assertEqual(list(root.glob('.env.telegram-switch.*')), [])


    def test_transport_cursor_reset_requires_stopped_poller_and_retains_audit(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('telegram_switch_cursor', SOURCE / 'scripts/telegram_local.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(module, 'ROOT', root), patch.object(module, 'compose', side_effect=[
                    subprocess.CompletedProcess([],0,stdout='backend\n'),
                    subprocess.CompletedProcess([],0,stdout='123456\n')]):
                module.reset_runtime_cursor('cloud', 'local')
            import json
            audit=root/'.telegram-transport-cursors.json'
            record=json.loads(audit.read_text())[0]
            self.assertEqual(record['previous_offset'],123456)
            self.assertEqual(record['offset'],0)
            self.assertEqual(audit.stat().st_mode & 0o777,0o600)
            with patch.object(module, 'ROOT', root), patch.object(module, 'compose', return_value=
                    subprocess.CompletedProcess([],0,stdout='backend\ntelegram-bot\n')) as call:
                with self.assertRaises(RuntimeError): module.reset_runtime_cursor('local','cloud')
                self.assertEqual(call.call_count,1)
