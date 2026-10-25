"""Run in the isolated worker image with its normal FFmpeg wrappers."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

from yt_dlp import YoutubeDL, parse_options
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.networking.impersonate import ImpersonateTarget


class WorkerMediaTests(unittest.TestCase):
    def browser_downloader(self):
        options = parse_options(['--config-locations', '/etc/yt-dlp.conf']).ydl_opts
        self.assertEqual(options['proxy'], 'http://download-proxy:8080')
        options['quiet'] = True
        return YoutubeDL(options)

    def test_browser_transport_available(self):
        # Some extractors require this transport even without --impersonate.
        with YoutubeDL({'quiet': True}) as downloader:
            targets = downloader._get_available_impersonate_targets()
        self.assertTrue(any(handler == 'curl_cffi' for _, handler in targets), targets)

    def test_browser_transport_public_https_via_proxy(self):
        with self.browser_downloader() as downloader:
            request = Request('https://raw.githubusercontent.com/naum-mm/box4one/main/README.md',
                              extensions={'impersonate': ImpersonateTarget()})
            with downloader.urlopen(request) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b'BOX4ONE', response.read(16384))

    def test_browser_transport_private_targets_blocked(self):
        with self.browser_downloader() as downloader:
            for target in ('http://backend/', 'http://127.0.0.1/', 'http://169.254.169.254/'):
                with self.subTest(target=target), self.assertRaises(HTTPError) as ctx:
                    downloader.urlopen(Request(target, extensions={'impersonate': ImpersonateTarget()}))
                self.assertEqual(ctx.exception.status, 403)

    def test_local_audio_conversion_and_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.wav'
            target = Path(directory) / 'sample.mp3'
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x00\x00' * 8000)
            converted = subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-y', '-i', str(source),
                                        '-c:a', 'libmp3lame', str(target)], capture_output=True, timeout=30)
            self.assertEqual(converted.returncode, 0, converted.stderr.decode())
            probe = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', '-show_entries', 'format=format_name',
                '-of', 'default=nw=1:nk=1', str(target)], capture_output=True, timeout=15)
            self.assertEqual(probe.returncode, 0, probe.stderr.decode())
            self.assertEqual(probe.stdout.strip(), b'mp3')

    def test_remote_ffmpeg_protocol_is_disabled(self):
        probe = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', 'http://backend:8000/health'],
                               capture_output=True, timeout=15)
        self.assertNotEqual(probe.returncode, 0)
        self.assertIn(b'not on whitelist', probe.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
