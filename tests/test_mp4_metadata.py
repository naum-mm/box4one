import struct
import tempfile
import unittest
from pathlib import Path
from app.mp4_metadata import video_dimensions


def box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def video(width, height, version=0, rotated=False, handler=b'vide'):
    offset = 40 if version == 0 else 52
    track = bytearray(offset + 44)
    track[0] = version
    matrix = (0, 65536, 0, -65536, 0, 0, 0, 0, 1073741824) if rotated else (
        65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824)
    struct.pack_into('>9iII', track, offset, *matrix, width * 65536, height * 65536)
    return box(b'moov', box(b'trak', box(b'tkhd', track) +
        box(b'mdia', box(b'hdlr', b'\0' * 8 + handler))))


class MP4MetadataTests(unittest.TestCase):
    def dimensions(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.mp4'
            path.write_bytes(data)
            return video_dimensions(path)

    def test_landscape_portrait_square_and_version_one(self):
        for width, height, version in [(640, 360, 0), (854, 480, 1), (1080, 1920, 0), (720, 720, 0)]:
            with self.subTest(width=width, height=height, version=version):
                self.assertEqual(self.dimensions(video(width, height, version)),
                                 {'width': width, 'height': height})

    def test_rotation_swaps_display_dimensions(self):
        self.assertEqual(self.dimensions(video(1920, 1080, rotated=True)), {'width': 1080, 'height': 1920})

    def test_skips_audio_and_media_payload(self):
        audio = video(10, 10, handler=b'soun')
        self.assertEqual(self.dimensions(box(b'mdat', b'x' * 1024) + audio + video(640, 360)),
                         {'width': 640, 'height': 360})

    def test_corrupt_truncated_and_excessive_boxes_are_ignored(self):
        for data in [b'', b'data', video(640, 360)[:-1], struct.pack('>I4s', 2, b'moov'),
                     box(b'free', b'') * 4097 + video(640, 360), box(b'moov', box(b'trak', box(b'tkhd', b'')))]:
            with self.subTest(size=len(data)):
                self.assertEqual(self.dimensions(data), {})
