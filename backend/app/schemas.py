from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    HttpUrl,
    model_validator,
)


# =========================================================
# Public jobs
# =========================================================

class JobCreate(BaseModel):
    source_url: HttpUrl

    media_type: Literal[
        "video",
        "audio",
    ]

    quality: Literal[
        "1080",
        "720",
        "480",
        "320",
        "128",
    ]

    @model_validator(mode="after")
    def validate_job(self):
        video_qualities = {
            "1080",
            "720",
            "480",
        }

        audio_qualities = {
            "320",
            "128",
        }

        if (
            self.media_type == "video"
            and self.quality not in video_qualities
        ):
            raise ValueError(
                "Invalid quality for video"
            )

        if (
            self.media_type == "audio"
            and self.quality not in audio_qualities
        ):
            raise ValueError(
                "Invalid quality for audio"
            )

        url = str(
            self.source_url
        )

        if any(
            char in url
            for char in "[]()"
        ):
            raise ValueError(
                "Invalid URL format"
            )

        parsed = urlparse(
            url
        )

        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise ValueError(
                "Only HTTP and HTTPS URLs are allowed"
            )

        return self


class JobCreateResponse(BaseModel):
    job_code: str
    status: str


class JobFileResponse(BaseModel):
    file_id: int
    filename: str
    size_bytes: int
    media_type: str
    download_url: str


class JobResponse(BaseModel):
    job_code: str
    source_url: str

    media_type: str
    quality: str
    status: str

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None

    total_items: int
    completed_items: int

    error_message: str | None

    files: list[
        JobFileResponse
    ]


# =========================================================
# Access words
# =========================================================

class AccessRequest(BaseModel):
    word: str


class AccessResponse(BaseModel):
    mode: Literal[
        "download",
        "queue",
    ]


# =========================================================
# Queue dashboard
# =========================================================

class QueueJobResponse(BaseModel):
    job_code: str

    status: str

    media_type: str
    quality: str

    created_at: datetime

    total_items: int
    completed_items: int

    queue_position: int | None


class QueueResponse(BaseModel):
    page: int
    pages: int
    page_size: int

    total: int

    active: int
    queued: int

    jobs: list[
        QueueJobResponse
    ]


# =========================================================
# Worker
# =========================================================

class WorkerJobResponse(BaseModel):
    job_code: str
    source_url: str
    media_type: str
    quality: str


class WorkerFile(BaseModel):
    filename: str
    relative_path: str
    size_bytes: int

    media_type: Literal[
        "video",
        "audio",
    ]


class WorkerCompleteRequest(BaseModel):
    files: list[
        WorkerFile
    ]


class WorkerFailedRequest(BaseModel):
    error_message: str


class WorkerProgressRequest(BaseModel):
    total_items: int
    completed_items: int


class WorkerStatusResponse(BaseModel):
    job_code: str
    status: str


class WorkerExpiredJobResponse(BaseModel):
    job_code: str
