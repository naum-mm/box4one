"""Read MP4 display dimensions with bounded reads, skipping media payloads."""
import struct


def video_dimensions(path):
    try:
        with path.open('rb') as source:
            source.seek(0, 2)
            length = source.tell()
            budget = 4096

            def boxes(start, end):
                nonlocal budget
                while start < end:
                    budget -= 1
                    if budget < 0 or end - start < 8:
                        raise ValueError('Invalid MP4 boxes')
                    source.seek(start)
                    size, kind = struct.unpack('>I4s', source.read(8))
                    header = 8
                    if size == 1:
                        size = struct.unpack('>Q', source.read(8))[0]
                        header = 16
                    elif size == 0:
                        size = end - start
                    if size < header or start + size > end:
                        raise ValueError('Invalid MP4 box size')
                    yield kind, start + header, start + size
                    start += size

            for kind, start, end in boxes(0, length):
                if kind != b'moov':
                    continue
                for kind, start, end in boxes(start, end):
                    if kind != b'trak':
                        continue
                    track = None
                    is_video = False
                    for kind, start, end in boxes(start, end):
                        if kind == b'tkhd':
                            source.seek(start)
                            track = source.read(min(96, end - start))
                        elif kind == b'mdia':
                            for kind, start, end in boxes(start, end):
                                if kind == b'hdlr' and end - start >= 12:
                                    source.seek(start + 8)
                                    is_video = source.read(4) == b'vide'
                    if not is_video or not track:
                        continue
                    version = track[0]
                    if version not in (0, 1):
                        continue
                    offset = 40 if version == 0 else 52
                    if len(track) < offset + 44:
                        continue
                    matrix = struct.unpack_from('>9i', track, offset)
                    width, height = struct.unpack_from('>II', track, offset + 36)
                    width, height = width / 65536, height / 65536
                    # tkhd dimensions include pixel aspect; its matrix carries rotation.
                    displayed_width = round((abs(matrix[0]) * width + abs(matrix[3]) * height) / 65536)
                    displayed_height = round((abs(matrix[1]) * width + abs(matrix[4]) * height) / 65536)
                    if 0 < displayed_width <= 65535 and 0 < displayed_height <= 65535:
                        return {'width': displayed_width, 'height': displayed_height}
    except (OSError, ValueError, struct.error):
        pass
    return {}
