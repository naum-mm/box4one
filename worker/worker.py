#!/usr/bin/env python3

import json
import fcntl
import logging
import os
import re
import subprocess
import signal
import shutil
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path


# =========================================================
# Configuration
# =========================================================

API_URL = os.environ["API_URL"].rstrip("/")
WORKER_API_TOKEN = os.environ["WORKER_API_TOKEN"]


JOBS_DIR = Path(
    os.environ["JOBS_DIR"]
)


POLL_INTERVAL = int(
    os.getenv(
        "POLL_INTERVAL",
        "5",
    )
)


VIDEO_PLAYLIST_LIMIT = int(
    os.getenv(
        "VIDEO_PLAYLIST_LIMIT",
        "50",
    )
)


AUDIO_PLAYLIST_LIMIT = int(
    os.getenv(
        "AUDIO_PLAYLIST_LIMIT",
        "100",
    )
)


YTDLP_BIN = "/usr/local/bin/yt-dlp"

FFMPEG_BIN = "/usr/bin/ffmpeg"

FFPROBE_BIN = "/usr/bin/ffprobe"


if VIDEO_PLAYLIST_LIMIT < 1:
    raise RuntimeError(
        "VIDEO_PLAYLIST_LIMIT must be greater than 0"
    )


if AUDIO_PLAYLIST_LIMIT < 1:
    raise RuntimeError(
        "AUDIO_PLAYLIST_LIMIT must be greater than 0"
    )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
)


# Storage limits include final and temporary files, in GiB.
def positive_setting(name, default):
    value = float(os.getenv(name, default))
    if not 0 < value < float("inf"):
        raise RuntimeError(f"{name} must be a finite positive number")
    return value

MIN_FREE_BYTES = int(positive_setting("MIN_FREE_GB", "2") * 1024**3)
MAX_JOB_BYTES = int(positive_setting("MAX_JOB_GB", "10") * 1024**3)
DISK_CHECK_SECONDS = positive_setting("DISK_CHECK_SECONDS", "1")


class StorageLimitError(RuntimeError):
    pass


def safe_job_directory(job_code):
    if not re.fullmatch(r"[a-z0-9]{10}", job_code):
        raise RuntimeError("Invalid job code")
    base = JOBS_DIR.resolve()
    directory = JOBS_DIR / job_code
    if directory.is_symlink() or directory.resolve().parent != base:
        raise RuntimeError("Unsafe job directory")
    return directory


def check_storage(directory):
    free = shutil.disk_usage(JOBS_DIR).free
    if free < MIN_FREE_BYTES:
        raise StorageLimitError("Задание остановлено: недостаточно свободного места на диске.")
    size = 0
    if directory.exists():
        for path in directory.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    size += path.stat().st_size
            except FileNotFoundError:
                continue
            if size > MAX_JOB_BYTES:
                raise StorageLimitError("Задание остановлено: превышен предел размера задания, включая временные файлы.")


class StorageGuard:
    def __init__(self, directory):
        self.directory = directory
        self.error = None
        self.processes = []
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def check(self):
        if self.error is not None:
            raise self.error
        check_storage(self.directory)

    def terminate(self):
        with self.lock:
            for process in self.processes:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def watch(self):
        while not self.stopped.wait(DISK_CHECK_SECONDS):
            try:
                self.check()
            except Exception as exc:
                self.error = exc
                self.terminate()
                return

    def close(self):
        self.stopped.set()
        self.thread.join()


active_guard = None


def start_process(command, **kwargs):
    # Human-readable tool output may contain non-UTF-8 metadata bytes.
    # Escape those bytes in logs instead of aborting the media job.
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "backslashreplace")
    if active_guard is None:
        return subprocess.Popen(command, start_new_session=True, **kwargs)
    with active_guard.lock:
        active_guard.check()
        process = subprocess.Popen(command, start_new_session=True, **kwargs)
        active_guard.processes.append(process)
    return process


def run_captured(command):
    # JSON stdout remains strict; only diagnostic stderr is decoded tolerantly.
    process = start_process(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate()
        if active_guard is not None:
            active_guard.check()
        return subprocess.CompletedProcess(
            command, process.returncode,
            stdout.decode("utf-8"),
            stderr.decode("utf-8", errors="backslashreplace"),
        )
    finally:
        if active_guard is not None:
            with active_guard.lock:
                active_guard.processes.remove(process)


# =========================================================
# Backend API
# =========================================================

def api_request(
    path,
    method="POST",
    payload=None,
    strict=False,
):

    headers = {
        "X-Worker-Token":
            WORKER_API_TOKEN,
    }


    data = None


    if payload is not None:

        headers[
            "Content-Type"
        ] = "application/json"


        data = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        )


    request = urllib.request.Request(
        f"{API_URL}{path}",
        method=method,
        headers=headers,
        data=data,
    )


    try:

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            if response.status == 204:
                return None


            body = response.read()


            if not body:
                return None


            return json.loads(
                body.decode(
                    "utf-8"
                )
            )


    except urllib.error.HTTPError as exc:

        if exc.code == 204:
            return None


        try:

            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

        except Exception:

            body = ""


        logging.error(
            "API %s returned HTTP %s: %s",
            path,
            exc.code,
            body,
        )


        if strict:
            raise
        return None


    except Exception:

        logging.exception(
            "Failed API request: %s",
            path,
        )


        if strict:
            raise
        return None


def get_next_job():

    return api_request(
        "/api/worker/jobs/next"
    )


def report_progress(
    job_code,
    total_items,
    completed_items,
):

    return api_request(
        f"/api/worker/jobs/{job_code}/progress",

        payload={
            "total_items":
                total_items,

            "completed_items":
                completed_items,
        },
    )


def report_complete(
    job_code,
    files,
):

    return api_request(
        f"/api/worker/jobs/{job_code}/complete",

        payload={
            "files":
                files,
        },
    )


def report_failed(
    job_code,
    message,
):

    return api_request(
        f"/api/worker/jobs/{job_code}/failed",

        payload={
            "error_message":
                message,
        },
    )


# =========================================================
# Command helpers
# =========================================================

def run_command(
    command,
    job_code,
):

    logging.info(
        "Running command for job %s",
        job_code,
    )


    process = start_process(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


    if process.stdout is None:

        raise RuntimeError(
            "Unable to capture process output"
        )


    for line in process.stdout:

        line = line.rstrip()


        if line:

            logging.info(
                "[%s] %s",
                job_code,
                line,
            )


    return_code = process.wait()
    if active_guard is not None:
        with active_guard.lock:
            active_guard.processes.remove(process)
        active_guard.check()


    if return_code != 0:

        raise RuntimeError(
            "Command failed with exit code "
            f"{return_code}"
        )


def run_download_command(
    command,
    job_code,
    total_items,
    is_playlist,
):

    logging.info(
        "Running download command for job %s",
        job_code,
    )


    process = start_process(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


    if process.stdout is None:

        raise RuntimeError(
            "Unable to capture yt-dlp output"
        )


    last_completed = 0


    item_pattern = re.compile(
        r"Downloading item\s+(\d+)\s+of\s+(\d+)"
    )


    for raw_line in process.stdout:

        line = raw_line.rstrip()


        if not line:
            continue


        logging.info(
            "[%s] %s",
            job_code,
            line,
        )


        if not is_playlist:
            continue


        match = item_pattern.search(
            line
        )


        if match is None:
            continue


        current_item = int(
            match.group(1)
        )


        # Когда начинается следующий элемент,
        # предыдущий элемент уже обработан yt-dlp.
        #
        # Это может быть успешно скачанный элемент
        # или unavailable элемент.
        #
        # Поэтому это прогресс обработки playlist,
        # а не окончательное число готовых файлов.

        completed_items = max(
            0,
            current_item - 1,
        )


        if (
            completed_items
            > last_completed
        ):

            report_progress(
                job_code,
                total_items,
                completed_items,
            )


            last_completed = (
                completed_items
            )


            logging.info(
                "Job %s progress: %d/%d",
                job_code,
                completed_items,
                total_items,
            )


    return_code = process.wait()
    if active_guard is not None:
        with active_guard.lock:
            active_guard.processes.remove(process)
        active_guard.check()


    if return_code == 0:
        return


    # Одиночный элемент:
    # ненулевой exit code = ошибка.

    if not is_playlist:

        raise RuntimeError(
            "yt-dlp failed with exit code "
            f"{return_code}"
        )


    # Playlist может содержать unavailable элементы.
    # yt-dlp при этом способен вернуть exit code 1,
    # хотя остальные файлы скачались нормально.

    logging.warning(
        "yt-dlp returned exit code %d for playlist job %s; "
        "continuing with produced files",
        return_code,
        job_code,
    )


def run_json_command(
    command,
    job_code,
):

    result = run_captured(command)


    if result.stderr:

        for line in (
            result.stderr.splitlines()
        ):

            logging.debug(
                "[%s] %s",
                job_code,
                line,
            )


    if result.returncode != 0:

        error = (
            result.stderr.strip()
        )


        if not error:

            error = (
                "Command exited with code "
                f"{result.returncode}"
            )


        raise RuntimeError(
            error[-3000:]
        )


    try:

        return json.loads(
            result.stdout
        )


    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "Unable to parse JSON command output"
        ) from exc


# =========================================================
# Playlist preflight
# =========================================================

def get_playlist_limit(
    media_type,
):

    if media_type == "video":

        return VIDEO_PLAYLIST_LIMIT


    if media_type == "audio":

        return AUDIO_PLAYLIST_LIMIT


    raise RuntimeError(
        f"Unsupported media type: {media_type}"
    )


def preflight_job(
    job,
):

    job_code = job[
        "job_code"
    ]

    media_type = job[
        "media_type"
    ]

    source_url = job[
        "source_url"
    ]


    limit = get_playlist_limit(
        media_type
    )


    inspection_limit = (
        limit + 1
    )


    command = [
        YTDLP_BIN,

        "--flat-playlist",

        "--playlist-end",
        str(
            inspection_limit
        ),

        "--dump-single-json",

        "--no-warnings",

        source_url,
    ]


    logging.info(
        "Preflight for job %s, limit=%d",
        job_code,
        limit,
    )


    info = run_json_command(
        command,
        job_code,
    )


    entries = info.get(
        "entries"
    )


    if isinstance(
        entries,
        list,
    ):

        valid_entries = [
            entry
            for entry
            in entries
            if entry is not None
        ]


        total_items = len(
            valid_entries
        )


        is_playlist = True


    else:

        total_items = 1

        is_playlist = False


    if total_items < 1:

        raise RuntimeError(
            "No downloadable items found"
        )


    if total_items > limit:

        if media_type == "video":

            raise RuntimeError(
                "Playlist limit exceeded: "
                f"maximum {VIDEO_PLAYLIST_LIMIT} "
                "video items"
            )


        raise RuntimeError(
            "Playlist limit exceeded: "
            f"maximum {AUDIO_PLAYLIST_LIMIT} "
            "audio items"
        )


    logging.info(
        "Preflight job %s: %s, items=%d",
        job_code,
        (
            "playlist"
            if is_playlist
            else "single item"
        ),
        total_items,
    )


    return {
        "is_playlist":
            is_playlist,

        "total_items":
            total_items,

        "limit":
            limit,
    }


# =========================================================
# Filename sanitation
# =========================================================

def is_emoji_character(
    char,
):

    code = ord(
        char
    )


    ranges = (
        (0x1F300, 0x1FAFF),
        (0x2600, 0x26FF),
        (0x2700, 0x27BF),
        (0xFE00, 0xFE0F),
        (0x1F1E6, 0x1F1FF),
    )


    return any(
        start <= code <= end
        for start, end
        in ranges
    )


def sanitize_stem(
    name,
):

    name = unicodedata.normalize(
        "NFKC",
        name,
    )


    name = "".join(
        char
        for char
        in name
        if not is_emoji_character(
            char
        )
    )


    name = "".join(
        char
        for char
        in name
        if (
            unicodedata.category(
                char
            )[0]
            != "C"
        )
    )


    name = re.sub(
        r'[\\/:*?"<>|]',
        " ",
        name,
    )


    name = re.sub(
        r"\s+",
        " ",
        name,
    )


    name = name.strip(
        " ._-"
    )


    if not name:

        name = "media"


    return name[
        :180
    ].rstrip()


def unique_destination(
    directory,
    stem,
    suffix,
):

    destination = (
        directory
        / f"{stem}{suffix}"
    )


    counter = 2


    while destination.exists():

        destination = (
            directory
            / f"{stem} ({counter}){suffix}"
        )


        counter += 1


    return destination


def sanitize_output_files(
    job_dir,
    extensions,
):

    paths = [
        path
        for path
        in job_dir.iterdir()
        if (
            path.is_file()
            and path.suffix.lower()
            in extensions
        )
    ]


    for path in paths:

        clean_stem = sanitize_stem(
            path.stem
        )


        clean_name = (
            clean_stem
            + path.suffix.lower()
        )


        if clean_name == path.name:
            continue


        destination = unique_destination(
            job_dir,
            clean_stem,
            path.suffix.lower(),
        )


        logging.info(
            "Renaming: %s -> %s",
            path.name,
            destination.name,
        )


        path.rename(
            destination
        )


# =========================================================
# Output templates
# =========================================================

def get_output_template(
    job_dir,
    is_playlist,
):

    if is_playlist:

        return str(
            job_dir
            / "%(playlist_index)03d - %(title)s.%(ext)s"
        )


    return str(
        job_dir
        / "%(title)s.%(ext)s"
    )


# =========================================================
# Video format selection
# =========================================================

def build_video_format(
    quality,
):

    # Приоритет:
    #
    # 1. H.264/AVC MP4 video + AAC/M4A audio
    # 2. Combined H.264 + AAC MP4
    # 3. Любой best video + best audio
    # 4. Любой combined best
    #
    # Пункты 3/4 нужны как fallback.
    #
    # После скачивания каждый MP4 всё равно
    # проверяется ffprobe. Если кодеки несовместимы
    # с Apple/Safari, worker перекодирует файл.

    return (
        f"bestvideo"
        f"[height<={quality}]"
        f"[vcodec^=avc1]"
        f"[ext=mp4]"

        f"+"

        f"bestaudio"
        f"[acodec^=mp4a]"
        f"[ext=m4a]"

        f"/"

        f"best"
        f"[height<={quality}]"
        f"[vcodec^=avc1]"
        f"[acodec^=mp4a]"
        f"[ext=mp4]"

        f"/"

        f"bestvideo"
        f"[height<={quality}]"

        f"+"

        f"bestaudio"

        f"/"

        f"best"
        f"[height<={quality}]"
    )


# =========================================================
# Video probing
# =========================================================

def probe_media(
    job_code,
    path,
):

    command = [
        FFPROBE_BIN,

        "-v",
        "error",

        "-show_streams",

        "-of",
        "json",

        str(
            path
        ),
    ]


    return run_json_command(
        command,
        job_code,
    )


def get_primary_streams(
    probe,
):

    video_stream = None

    audio_stream = None


    for stream in probe.get(
        "streams",
        [],
    ):

        codec_type = stream.get(
            "codec_type"
        )


        if (
            codec_type == "video"
            and video_stream is None
        ):

            video_stream = stream


        elif (
            codec_type == "audio"
            and audio_stream is None
        ):

            audio_stream = stream


    return (
        video_stream,
        audio_stream,
    )


def is_apple_compatible_video(
    probe,
):

    (
        video_stream,
        audio_stream,
    ) = get_primary_streams(
        probe
    )


    if video_stream is None:

        return False


    video_codec = (
        video_stream.get(
            "codec_name"
        )
        or ""
    ).lower()


    pixel_format = (
        video_stream.get(
            "pix_fmt"
        )
        or ""
    ).lower()


    if video_codec != "h264":

        return False


    # Для максимально широкой совместимости:
    # обычный 8-bit 4:2:0.

    if pixel_format != "yuv420p":

        return False


    # Видео без аудио допустимо.
    #
    # Если аудиопоток есть, требуем AAC.

    if audio_stream is not None:

        audio_codec = (
            audio_stream.get(
                "codec_name"
            )
            or ""
        ).lower()


        if audio_codec != "aac":

            return False


    return True


# =========================================================
# MP4 Apple optimization
# =========================================================

def remux_video_faststart(
    job_code,
    source_path,
):

    temp_path = (
        source_path.parent
        / (
            "."
            + source_path.stem
            + ".faststart.mp4"
        )
    )


    command = [
        FFMPEG_BIN,

        "-y",

        "-i",
        str(
            source_path
        ),

        "-map",
        "0:v:0",

        "-map",
        "0:a:0?",

        "-map_metadata",
        "0",

        "-c",
        "copy",

        "-tag:v",
        "avc1",

        "-movflags",
        "+faststart",

        str(
            temp_path
        ),
    ]


    logging.info(
        "Optimizing compatible MP4 for Apple playback: %s",
        source_path.name,
    )


    try:

        run_command(
            command,
            job_code,
        )


        temp_path.replace(
            source_path
        )


    finally:

        temp_path.unlink(
            missing_ok=True
        )


def transcode_video_for_apple(
    job_code,
    source_path,
):

    temp_path = (
        source_path.parent
        / (
            "."
            + source_path.stem
            + ".apple.mp4"
        )
    )


    command = [
        FFMPEG_BIN,

        "-y",

        "-i",
        str(
            source_path
        ),

        "-map",
        "0:v:0",

        "-map",
        "0:a:0?",

        "-map_metadata",
        "0",

        # -----------------------------
        # Video
        # -----------------------------

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "20",

        "-pix_fmt",
        "yuv420p",

        "-tag:v",
        "avc1",

        # -----------------------------
        # Audio
        # -----------------------------

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        # -----------------------------
        # MP4 layout
        # -----------------------------

        "-movflags",
        "+faststart",

        str(
            temp_path
        ),
    ]


    logging.warning(
        "Transcoding incompatible video "
        "to H.264/yuv420p + AAC: %s",
        source_path.name,
    )


    try:

        run_command(
            command,
            job_code,
        )


        probe = probe_media(
            job_code,
            temp_path,
        )


        if not is_apple_compatible_video(
            probe
        ):

            raise RuntimeError(
                "Transcoded video is still not "
                "H.264/yuv420p with AAC audio"
            )


        temp_path.replace(
            source_path
        )


    finally:

        temp_path.unlink(
            missing_ok=True
        )


def ensure_apple_compatible_videos(
    job_code,
    job_dir,
):

    video_files = sorted(
        path
        for path
        in job_dir.iterdir()
        if (
            path.is_file()

            and path.suffix.lower()
            == ".mp4"

            and not path.name.startswith(
                "."
            )
        )
    )


    if not video_files:

        raise RuntimeError(
            "No MP4 files found after video download"
        )


    for path in video_files:

        probe = probe_media(
            job_code,
            path,
        )


        (
            video_stream,
            audio_stream,
        ) = get_primary_streams(
            probe
        )


        video_codec = (
            video_stream.get(
                "codec_name"
            )
            if video_stream
            else None
        )


        pixel_format = (
            video_stream.get(
                "pix_fmt"
            )
            if video_stream
            else None
        )


        audio_codec = (
            audio_stream.get(
                "codec_name"
            )
            if audio_stream
            else None
        )


        logging.info(
            "Video probe %s: "
            "video=%s pix_fmt=%s audio=%s",

            path.name,
            video_codec,
            pixel_format,
            audio_codec,
        )


        if is_apple_compatible_video(
            probe
        ):

            remux_video_faststart(
                job_code,
                path,
            )


            continue


        transcode_video_for_apple(
            job_code,
            path,
        )


# =========================================================
# Video
# =========================================================

def download_video(
    job,
    job_dir,
    preflight,
):

    job_code = job[
        "job_code"
    ]

    quality = job[
        "quality"
    ]

    source_url = job[
        "source_url"
    ]


    output_template = (
        get_output_template(
            job_dir,
            preflight[
                "is_playlist"
            ],
        )
    )


    command = [
        YTDLP_BIN,

        "--ffmpeg-location",
        FFMPEG_BIN,

        "--newline",

        "--no-overwrites",

        "--windows-filenames",

        "--trim-filenames",
        "180",

        "--merge-output-format",
        "mp4",

        # Если yt-dlp получил одиночный WebM/MKV
        # fallback-формат, пытаемся привести контейнер
        # к MP4 без перекодирования.
        #
        # После этого кодеки проверит ffprobe.

        "--remux-video",
        "mp4",

        "-f",
        build_video_format(
            quality
        ),
    ]


    if preflight[
        "is_playlist"
    ]:

        command.extend(
            [
                "--playlist-end",

                str(
                    VIDEO_PLAYLIST_LIMIT
                ),
            ]
        )


    command.extend(
        [
            "-o",

            output_template,

            source_url,
        ]
    )


    logging.info(
        "Starting video download for job %s",
        job_code,
    )


    run_download_command(
        command,
        job_code,
        preflight[
            "total_items"
        ],
        preflight[
            "is_playlist"
        ],
    )


    # -----------------------------------------------------
    # Проверка кодеков
    # -----------------------------------------------------
    #
    # Нормальный сценарий:
    #
    # H.264 + AAC уже пришли от источника.
    # Тогда перекодирования НЕТ.
    #
    # Только выполняется быстрый remux + faststart.
    #
    # Fallback:
    #
    # AV1 / VP9 / Opus / 10-bit и т.п.
    # автоматически перекодируются.

    ensure_apple_compatible_videos(
        job_code,
        job_dir,
    )


    sanitize_output_files(
        job_dir,
        {
            ".mp4",
        },
    )


    logging.info(
        "Video processing finished for job %s",
        job_code,
    )


# =========================================================
# Audio
# =========================================================

def download_audio(
    job,
    job_dir,
    preflight,
):

    job_code = job[
        "job_code"
    ]

    bitrate = job[
        "quality"
    ]

    source_url = job[
        "source_url"
    ]


    output_template = (
        get_output_template(
            job_dir,
            preflight[
                "is_playlist"
            ],
        )
    )


    command = [
        YTDLP_BIN,

        "--ffmpeg-location",
        FFMPEG_BIN,

        "--newline",

        "--no-overwrites",

        "--windows-filenames",

        "--trim-filenames",
        "180",

        "--extract-audio",

        "--audio-format",
        "mp3",

        "--audio-quality",
        f"{bitrate}K",

        "--embed-metadata",

        "--write-thumbnail",

        "--convert-thumbnails",
        "jpg",
    ]


    if preflight[
        "is_playlist"
    ]:

        command.extend(
            [
                "--playlist-end",

                str(
                    AUDIO_PLAYLIST_LIMIT
                ),
            ]
        )


    command.extend(
        [
            "-o",

            output_template,

            source_url,
        ]
    )


    logging.info(
        "Starting audio download "
        "for job %s, bitrate=%sK",
        job_code,
        bitrate,
    )


    run_download_command(
        command,
        job_code,
        preflight[
            "total_items"
        ],
        preflight[
            "is_playlist"
        ],
    )


    # Даже если часть playlist unavailable,
    # обрабатываем все реально полученные MP3.

    process_audio_covers(
        job_code,
        job_dir,
    )


    sanitize_output_files(
        job_dir,
        {
            ".mp3",
        },
    )


    cleanup_audio_artifacts(
        job_dir,
    )


    logging.info(
        "Audio processing finished for job %s",
        job_code,
    )


# =========================================================
# Audio covers
# =========================================================

def process_audio_covers(
    job_code,
    job_dir,
):

    mp3_files = sorted(
        job_dir.glob(
            "*.mp3"
        )
    )


    if not mp3_files:

        raise RuntimeError(
            "No MP3 files found after audio conversion"
        )


    for mp3_path in mp3_files:

        thumbnail = (
            find_thumbnail_for_audio(
                job_dir,
                mp3_path,
            )
        )


        if thumbnail is None:

            logging.warning(
                "No thumbnail found for %s",
                mp3_path.name,
            )


            continue


        embed_square_cover(
            job_code,
            mp3_path,
            thumbnail,
        )


def find_thumbnail_for_audio(
    job_dir,
    mp3_path,
):

    exact_candidates = [
        job_dir
        / f"{mp3_path.stem}.jpg",

        job_dir
        / f"{mp3_path.stem}.jpeg",

        job_dir
        / f"{mp3_path.stem}.png",

        job_dir
        / f"{mp3_path.stem}.webp",
    ]


    for candidate in exact_candidates:

        if candidate.exists():

            return candidate


    return None


def embed_square_cover(
    job_code,
    mp3_path,
    thumbnail_path,
):

    square_cover = (
        mp3_path.parent
        / f".{mp3_path.stem}.cover.jpg"
    )


    temp_mp3 = (
        mp3_path.parent
        / f".{mp3_path.stem}.tagged.mp3"
    )


    cover_command = [
        FFMPEG_BIN,

        "-y",

        "-i",
        str(
            thumbnail_path
        ),

        "-vf",
        (
            "crop="
            "'min(iw,ih)':"
            "'min(iw,ih)',"
            "scale=1000:1000"
        ),

        "-frames:v",
        "1",

        "-update",
        "1",

        "-q:v",
        "2",

        str(
            square_cover
        ),
    ]


    logging.info(
        "Creating square cover for %s",
        mp3_path.name,
    )


    run_command(
        cover_command,
        job_code,
    )


    embed_command = [
        FFMPEG_BIN,

        "-y",

        "-i",
        str(
            mp3_path
        ),

        "-i",
        str(
            square_cover
        ),

        "-map",
        "0:a:0",

        "-map",
        "1:v:0",

        "-map_metadata",
        "0",

        "-c:a",
        "copy",

        "-c:v",
        "mjpeg",

        "-id3v2_version",
        "3",

        "-disposition:v:0",
        "attached_pic",

        "-metadata:s:v",
        "title=Album cover",

        "-metadata:s:v",
        "comment=Cover (front)",

        str(
            temp_mp3
        ),
    ]


    logging.info(
        "Embedding cover into %s",
        mp3_path.name,
    )


    run_command(
        embed_command,
        job_code,
    )


    temp_mp3.replace(
        mp3_path
    )


    square_cover.unlink(
        missing_ok=True
    )


    thumbnail_path.unlink(
        missing_ok=True
    )


def cleanup_audio_artifacts(
    job_dir,
):

    removable_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".part",
        ".ytdl",
    }


    for path in job_dir.iterdir():

        if not path.is_file():
            continue


        if (
            path.suffix.lower()
            in removable_extensions
        ):

            logging.info(
                "Removing temporary file: %s",
                path.name,
            )


            path.unlink(
                missing_ok=True
            )


# =========================================================
# Collect finished files
# =========================================================

def collect_files(
    job,
    job_dir,
):

    files = []


    media_type = job[
        "media_type"
    ]


    allowed_extensions = {

        "video": {
            ".mp4",
        },

        "audio": {
            ".mp3",
        },
    }


    allowed = (
        allowed_extensions[
            media_type
        ]
    )


    for path in sorted(
        job_dir.iterdir()
    ):

        if not path.is_file():
            continue


        # Не публикуем временные скрытые файлы.

        if path.name.startswith(
            "."
        ):
            continue


        if (
            path.suffix.lower()
            not in allowed
        ):
            continue


        files.append(
            {
                "filename":
                    path.name,

                "relative_path":
                    path.name,

                "size_bytes":
                    path.stat().st_size,

                "media_type":
                    media_type,
            }
        )


    return files


# =========================================================
# Process job
# =========================================================

def process_job(
    job,
):

    job_code = job[
        "job_code"
    ]

    global active_guard
    job_dir = safe_job_directory(job_code)
    active_guard = StorageGuard(job_dir)
    active_guard.thread.start()


    logging.info(
        "Job %s received: type=%s quality=%s",
        job_code,
        job[
            "media_type"
        ],
        job[
            "quality"
        ],
    )


    ready = False
    try:

        active_guard.check()
        preflight = preflight_job(
            job
        )


        logging.info(
            "Job %s accepted: %d item(s), limit=%d",
            job_code,
            preflight[
                "total_items"
            ],
            preflight[
                "limit"
            ],
        )


        report_progress(
            job_code,
            preflight[
                "total_items"
            ],
            0,
        )


        job_dir = (
            JOBS_DIR
            / job_code
        )


        job_dir.mkdir(
            parents=True,
            exist_ok=True,
        )


        logging.info(
            "Job directory: %s",
            job_dir,
        )


        if (
            job[
                "media_type"
            ]
            == "video"
        ):

            download_video(
                job,
                job_dir,
                preflight,
            )


        elif (
            job[
                "media_type"
            ]
            == "audio"
        ):

            download_audio(
                job,
                job_dir,
                preflight,
            )


        else:

            raise RuntimeError(
                "Unsupported media type"
            )


        files = collect_files(
            job,
            job_dir,
        )


        # Ключевая финальная проверка.
        #
        # Playlist может содержать unavailable элементы,
        # но если хотя бы один итоговый файл готов,
        # задание считается успешным.

        if not files:

            raise RuntimeError(
                "No output files found"
            )


        if (
            len(
                files
            )
            > preflight[
                "limit"
            ]
        ):

            raise RuntimeError(
                "Output file count exceeds "
                "configured playlist limit"
            )


        if (
            preflight[
                "is_playlist"
            ]

            and

            len(
                files
            )
            < preflight[
                "total_items"
            ]
        ):

            skipped = (
                preflight[
                    "total_items"
                ]
                - len(
                    files
                )
            )


            logging.warning(
                "Job %s completed partially: "
                "%d/%d file(s) produced, "
                "%d playlist item(s) unavailable or failed",

                job_code,

                len(
                    files
                ),

                preflight[
                    "total_items"
                ],

                skipped,
            )


        active_guard.check()
        # Finish processing before recording readiness. Never delete ready files
        # because a delivery attempt or backend response failed.
        active_guard.close()
        ready = True
        save_pending(job_code, "complete", {"files": files})
        logging.info("Job %s files ready; awaiting backend confirmation", job_code)


    except Exception as exc:

        if ready:
            # A persistence failure is fatal: preserve files and stop the queue.
            logging.exception("Unable to persist ready result for %s; worker stopped", job_code)
            raise SystemExit(1)
        if active_guard.error is not None:
            exc = active_guard.error
        active_guard.terminate()
        logging.exception("Job %s failed", job_code)
        save_pending(job_code, "failed", {"error_message": str(exc)[:4000]})
        try:
            directory = safe_job_directory(job_code)
            if directory.exists():
                shutil.rmtree(directory)
        except Exception:
            logging.exception("Unable to remove failed job directory %s", job_code)


    finally:
        active_guard.close()
        active_guard = None


# Durable delivery and startup reconciliation.
STATE_DIR = JOBS_DIR / ".worker-state"
RETRY_MAX_SECONDS = positive_setting("RETRY_MAX_SECONDS", "60")
worker_lock = None


def save_pending(job_code, action, payload):
    safe_job_directory(job_code)
    STATE_DIR.mkdir(exist_ok=True)
    destination = STATE_DIR / f"{job_code}.json"
    temporary = destination.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"job_code": job_code, "action": action, "payload": payload}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    sync_state_directory()


def sync_state_directory():
    descriptor = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def deliver_pending(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    code = record["job_code"]
    directory = safe_job_directory(code)
    action = record["action"]
    if action not in {"complete", "failed"} or path.name != f"{code}.json":
        raise RuntimeError("Invalid pending result")
    result = api_request(f"/api/worker/jobs/{code}/{action}", payload=record["payload"], strict=True)
    accepted = {"completed", "expired"} if action == "complete" else {"failed"}
    if not isinstance(result, dict) or result.get("job_code") != code or result.get("status") not in accepted:
        raise RuntimeError("Backend did not confirm the pending result")
    if action == "failed" and directory.exists():
        shutil.rmtree(directory)
    path.unlink()
    sync_state_directory()
    logging.info("Job %s confirmed by backend: %s", code, result["status"])


def reconcile():
    STATE_DIR.mkdir(exist_ok=True)
    for path in sorted(STATE_DIR.glob("*.json")):
        deliver_pending(path)
    active = api_request("/api/worker/jobs/active", method="GET", strict=True)
    if not isinstance(active, list):
        raise RuntimeError("Unable to reconcile active jobs")
    for job in active:
        code = job["job_code"]
        save_pending(code, "failed", {"error_message": "Задание прервано остановкой worker. Запустите загрузку повторно."})
        deliver_pending(STATE_DIR / f"{code}.json")


def wait_for_reconciliation():
    delay = max(1, POLL_INTERVAL)
    while True:
        try:
            reconcile()
            return
        except Exception:
            logging.exception("Queue paused: waiting for backend confirmation / reconciliation")
            time.sleep(delay)
            delay = min(RETRY_MAX_SECONDS, delay * 2)


def stop_worker(signum, frame):
    if active_guard is not None:
        active_guard.terminate()
    raise SystemExit(0)


# =========================================================
# Main
# =========================================================

def main():

    JOBS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


    global worker_lock
    worker_lock = (JOBS_DIR / ".worker.lock").open("a")
    try:
        fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another worker is already running")
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)

    logging.info(
        "Worker started"
    )


    logging.info(
        "API: %s",
        API_URL,
    )


    logging.info(
        "Jobs directory: %s",
        JOBS_DIR,
    )


    logging.info(
        "Video playlist limit: %d",
        VIDEO_PLAYLIST_LIMIT,
    )


    logging.info(
        "Audio playlist limit: %d",
        AUDIO_PLAYLIST_LIMIT,
    )


    while True:

        wait_for_reconciliation()
        if shutil.disk_usage(JOBS_DIR).free < MIN_FREE_BYTES:
            logging.warning("Queue paused: free disk space is below MIN_FREE_GB")
            time.sleep(max(POLL_INTERVAL, 30))
            continue

        job = get_next_job()


        if job is not None:

            process_job(
                job
            )


        time.sleep(
            POLL_INTERVAL
        )


if __name__ == "__main__":
    main()
