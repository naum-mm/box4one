import os
import secrets
import string
import threading
import zipfile

from collections import deque
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)

from fastapi.responses import StreamingResponse

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.security import verify_download_access
from app.database import get_db
from app.models import File, Job
from app.schemas import (
    JobCreate,
    JobCreateResponse,
    JobFileResponse,
    JobResponse,
)


router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
)


JOB_CODE_LENGTH = 10

JOB_CODE_ALPHABET = (
    string.ascii_lowercase
    + string.digits
)


JOBS_ROOT = Path(
    os.environ.get(
        "JOBS_ROOT",
        "/data/ytdlp/jobs",
    )
)


ARCHIVE_CHUNK_SIZE = (
    1024 * 1024
)


archive_gate = threading.BoundedSemaphore(
    value=1
)


# =========================================================
# Time / job helpers
# =========================================================

def utc_now() -> datetime:
    return datetime.utcnow()


def generate_job_code() -> str:
    return "".join(
        secrets.choice(
            JOB_CODE_ALPHABET
        )
        for _ in range(
            JOB_CODE_LENGTH
        )
    )


def create_unique_job_code(
    db: Session,
) -> str:

    for _ in range(100):

        job_code = generate_job_code()

        existing = db.scalar(
            select(Job.id).where(
                Job.job_code
                == job_code
            )
        )

        if existing is None:
            return job_code

    raise RuntimeError(
        "Unable to generate unique job code"
    )


def get_effective_status(
    job: Job,
) -> str:

    if (
        job.status == "completed"
        and job.expires_at is not None
        and job.expires_at <= utc_now()
    ):
        return "expired"

    return job.status


# =========================================================
# Public response helpers
# =========================================================

def build_file_response(
    job: Job,
    file_record: File,
) -> JobFileResponse:

    return JobFileResponse(
        file_id=file_record.id,
        filename=file_record.filename,
        size_bytes=file_record.size_bytes,
        media_type=file_record.media_type,
        download_url=(
            f"/api/jobs/{job.job_code}"
            f"/files/{file_record.id}/download"
        ),
    )


def build_job_response(
    job: Job,
) -> JobResponse:

    effective_status = (
        get_effective_status(
            job
        )
    )

    files = []

    if effective_status == "completed":

        files = [
            build_file_response(
                job,
                file_record,
            )
            for file_record in sorted(
                job.files,
                key=lambda item: item.id,
            )
            if file_record.status == "ready"
        ]

    return JobResponse(
        job_code=job.job_code,
        source_url=job.source_url,
        media_type=job.media_type,
        quality=job.quality,
        status=effective_status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        expires_at=job.expires_at,
        total_items=job.total_items,
        completed_items=job.completed_items,
        error_message=job.error_message,
        files=files,
    )


# =========================================================
# Path helpers
# =========================================================

def validate_relative_path(
    relative_path: str,
) -> PurePosixPath:

    path = PurePosixPath(
        relative_path
    )

    if path.is_absolute():

        raise HTTPException(
            status_code=500,
            detail="Invalid file path",
        )

    if ".." in path.parts:

        raise HTTPException(
            status_code=500,
            detail="Invalid file path",
        )

    return path


def resolve_job_file(
    job_code: str,
    relative_path: str,
) -> Path:

    safe_relative_path = (
        validate_relative_path(
            relative_path
        )
    )

    try:

        job_root = (
            JOBS_ROOT
            / job_code
        ).resolve(
            strict=True
        )

        file_path = (
            job_root
            / safe_relative_path.as_posix()
        ).resolve(
            strict=True
        )

    except FileNotFoundError:

        raise HTTPException(
            status_code=404,
            detail="File not found",
        )


    try:

        job_root.relative_to(JOBS_ROOT.resolve(strict=True))
        file_path.relative_to(
            job_root
        )

    except ValueError:

        raise HTTPException(
            status_code=500,
            detail="Invalid file path",
        )


    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="File not found",
        )


    return file_path


# =========================================================
# MIME helpers
# =========================================================

def get_download_media_type(
    filename: str,
) -> str:

    suffix = PurePosixPath(
        filename
    ).suffix.lower()

    media_types = {
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".m4a": "audio/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
    }

    return media_types.get(
        suffix,
        "application/octet-stream",
    )


# =========================================================
# Streaming ZIP helpers
# =========================================================

def make_unique_archive_name(
    filename: str,
    used_names: set[str],
) -> str:

    original = PurePosixPath(
        filename
    ).name

    if not original:
        original = "file"


    if original not in used_names:

        used_names.add(
            original
        )

        return original


    path = PurePosixPath(
        original
    )

    stem = path.stem
    suffix = path.suffix

    counter = 2


    while True:

        candidate = (
            f"{stem} ({counter}){suffix}"
        )

        if candidate not in used_names:

            used_names.add(
                candidate
            )

            return candidate

        counter += 1


class StreamingZipBuffer:

    def __init__(self):

        self._chunks = deque()

        self._position = 0


    def write(
        self,
        data,
    ):

        if not data:
            return 0

        chunk = bytes(
            data
        )

        self._chunks.append(
            chunk
        )

        self._position += len(
            chunk
        )

        return len(
            chunk
        )


    def tell(self):

        return self._position


    def flush(self):

        return None


    def drain(self):

        while self._chunks:

            yield self._chunks.popleft()


def close_archive_sources(
    sources,
):

    for _, source in sources:

        try:

            source.close()

        except Exception:

            pass


def build_archive_stream(
    sources,
):

    output = StreamingZipBuffer()

    try:

        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:

            for (
                archive_name,
                source,
            ) in sources:

                source.seek(
                    0
                )


                with archive.open(
                    archive_name,
                    mode="w",
                    force_zip64=True,
                ) as destination:

                    while True:

                        chunk = source.read(
                            ARCHIVE_CHUNK_SIZE
                        )

                        if not chunk:
                            break

                        destination.write(
                            chunk
                        )

                        yield from output.drain()


                yield from output.drain()


        yield from output.drain()


    finally:

        close_archive_sources(
            sources
        )

        archive_gate.release()


# =========================================================
# Create job
# =========================================================

def enqueue_job(db: Session, request: JobCreate) -> Job:
    job_code = create_unique_job_code(
        db
    )


    job = Job(
        job_code=job_code,
        source_url=str(
            request.source_url
        ),
        media_type=request.media_type,
        quality=request.quality,
        status="queued",
        total_items=0,
        completed_items=0,
    )


    db.add(
        job
    )

    db.flush()
    return job


def hide_telegram_job(db, job):
    from app.telegram_models import TelegramRequest
    if inspect(db.get_bind()).has_table(TelegramRequest.__tablename__):
        if db.scalar(select(TelegramRequest.id).where(TelegramRequest.job_id == job.id)):
            raise HTTPException(404, "Job not found")


@router.post(
    "",
    response_model=JobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_download_access)],
)
def create_job(
    request: JobCreate,
    db: Session = Depends(
        get_db
    ),
):

    job = enqueue_job(db, request)

    db.commit()

    db.refresh(
        job
    )


    return JobCreateResponse(
        job_code=job.job_code,
        status=job.status,
    )


# =========================================================
# Get job
# =========================================================

@router.get(
    "/{job_code}",
    response_model=JobResponse,
)
def get_job(
    job_code: str,
    db: Session = Depends(
        get_db
    ),
):

    job = db.scalar(
        select(Job).where(
            Job.job_code
            == job_code
        )
    )


    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    hide_telegram_job(db, job)


    return build_job_response(
        job
    )


# =========================================================
# Download all
# =========================================================

@router.get(
    "/{job_code}/archive",
)
def download_archive(
    job_code: str,
    db: Session = Depends(
        get_db
    ),
):

    job = db.scalar(
        select(Job).where(
            Job.job_code
            == job_code
        )
    )


    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    hide_telegram_job(db, job)


    return stream_archive(job, db)


def stream_archive(job, db):
    effective_status = (
        get_effective_status(
            job
        )
    )


    if effective_status == "expired":

        raise HTTPException(
            status_code=410,
            detail="Job has expired",
        )


    if effective_status != "completed":

        raise HTTPException(
            status_code=409,
            detail="Job is not completed",
        )


    ready_files = [
        file_record
        for file_record
        in sorted(
            job.files,
            key=lambda item: item.id,
        )
        if file_record.status
        == "ready"
    ]


    if len(
        ready_files
    ) < 2:

        raise HTTPException(
            status_code=409,
            detail=(
                "Archive download requires "
                "at least two files"
            ),
        )


    if not archive_gate.acquire(
        blocking=False
    ):

        raise HTTPException(
            status_code=409,
            detail=(
                "Another archive download "
                "is already running"
            ),
        )


    sources = []

    used_names = set()


    try:

        for file_record in ready_files:

            file_path = resolve_job_file(
                job.job_code,
                file_record.relative_path,
            )


            archive_name = (
                make_unique_archive_name(
                    file_record.filename,
                    used_names,
                )
            )


            source = open(
                file_path,
                "rb",
            )


            sources.append(
                (
                    archive_name,
                    source,
                )
            )


    except Exception:

        close_archive_sources(
            sources
        )

        archive_gate.release()

        raise


    archive_filename = (
        f"box4one-{job.job_code}.zip"
    )


    encoded_filename = quote(
        archive_filename,
        safe="",
    )


    content_disposition = (
        "attachment; "
        f'filename="{archive_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


    return StreamingResponse(
        build_archive_stream(
            sources
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                content_disposition,

            "X-Accel-Buffering":
                "no",

            "Cache-Control":
                "no-store",

            "X-Content-Type-Options":
                "nosniff",
        },
    )


# =========================================================
# Single file download
# =========================================================

@router.get(
    "/{job_code}/files/{file_id}/download",
)
def download_file(
    job_code: str,
    file_id: int,
    preview: bool = False,
    db: Session = Depends(
        get_db
    ),
):

    job = db.scalar(
        select(Job).where(
            Job.job_code
            == job_code
        )
    )


    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    hide_telegram_job(db, job)


    effective_status = (
        get_effective_status(
            job
        )
    )


    if effective_status == "expired":

        raise HTTPException(
            status_code=410,
            detail="Job has expired",
        )


    if effective_status != "completed":

        raise HTTPException(
            status_code=409,
            detail="Job is not completed",
        )


    file_record = db.scalar(
        select(File).where(
            File.id == file_id,
            File.job_id == job.id,
            File.status == "ready",
        )
    )


    if file_record is None:

        raise HTTPException(
            status_code=404,
            detail="File not found",
        )


    relative_path = (
        validate_relative_path(
            file_record.relative_path
        )
    )


    encoded_relative_path = quote(
        relative_path.as_posix(),
        safe="/",
    )


    internal_uri = (
        "/_protected_downloads/"
        f"{job.job_code}/"
        f"{encoded_relative_path}"
    )


    encoded_filename = quote(
        file_record.filename,
        safe="",
    )


    suffix = PurePosixPath(
        file_record.filename
    ).suffix.lower()


    fallback_filename = (
        f"download{suffix}"
        if suffix
        else "download"
    )


    media_type = (
        get_download_media_type(
            file_record.filename
        )
    )


    if preview and suffix not in {".mp4", ".mp3"}:
        raise HTTPException(status_code=400, detail="Preview is only available for MP4 and MP3")

    content_disposition = (
        ("inline; " if preview else "attachment; ") +
        f'filename="{fallback_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect":
                internal_uri,

            "Content-Type":
                media_type,

            "Content-Disposition":
                content_disposition,

            "Cache-Control":
                "no-store",

            "X-Content-Type-Options":
                "nosniff",
        },
    )
