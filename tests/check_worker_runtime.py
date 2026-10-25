"""Run in the isolated worker image with its normal FFmpeg wrappers."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave


class WorkerMediaTests(unittest.TestCase):
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
