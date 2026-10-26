import os
from datetime import datetime, timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import File, Job
from app.schemas import (
    WorkerCompleteRequest,
    WorkerExpiredJobResponse,
    WorkerFailedRequest,
    WorkerJobResponse,
    WorkerProgressRequest,
    WorkerStatusResponse,
)
from app.security import verify_worker_token


JOB_TTL_MINUTES = int(
    os.getenv(
        "JOB_TTL_MINUTES",
        "60",
    )
)

if JOB_TTL_MINUTES < 1:
    raise RuntimeError(
        "JOB_TTL_MINUTES must be greater than 0"
    )


router = APIRouter(
    prefix="/api/worker",
    tags=["worker"],
    dependencies=[
        Depends(verify_worker_token),
    ],
)


def utc_now() -> datetime:
    return datetime.utcnow()


# =========================================================
# Claim next download job
# =========================================================

@router.post(
    "/jobs/next",
    response_model=WorkerJobResponse,
)
def get_next_job(
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(Job)
        .where(
            Job.status == "queued"
        )
        .order_by(Job.id)
        .limit(1)
        .with_for_update(
            skip_locked=True
        )
    )

    if job is None:
        db.rollback()

        return Response(
            status_code=status.HTTP_204_NO_CONTENT
        )

    job.status = "downloading"
    job.started_at = utc_now()

    job.total_items = 0
    job.completed_items = 0

    job.error_message = None

    db.commit()
    db.refresh(job)

    return WorkerJobResponse(
        job_code=job.job_code,
        source_url=job.source_url,
        media_type=job.media_type,
        quality=job.quality,
    )


# =========================================================
# Download progress
# =========================================================

@router.post(
    "/jobs/{job_code}/progress",
    response_model=WorkerStatusResponse,
)
def update_progress(
    job_code: str,
    request: WorkerProgressRequest,
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(Job).where(
            Job.job_code == job_code
        )
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    if job.status not in {
        "downloading",
        "processing",
    }:
        raise HTTPException(
            status_code=409,
            detail="Job is not active",
        )

    if request.total_items < 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "total_items must be greater than 0"
            ),
        )

    if request.completed_items < 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "completed_items cannot be negative"
            ),
        )

    if (
        request.completed_items
        > request.total_items
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "completed_items cannot exceed "
                "total_items"
            ),
        )

    job.total_items = (
        request.total_items
    )

    job.completed_items = (
        request.completed_items
    )

    db.commit()

    return WorkerStatusResponse(
        job_code=job.job_code,
        status=job.status,
    )


# =========================================================
# Complete job
# =========================================================

@router.post(
    "/jobs/{job_code}/complete",
    response_model=WorkerStatusResponse,
)
def complete_job(
    job_code: str,
    request: WorkerCompleteRequest,
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(Job).where(
            Job.job_code == job_code
        ).with_for_update()
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    if job.status in {"completed", "expired"}:
        return WorkerStatusResponse(job_code=job.job_code, status=job.status)
    if job.status not in {"downloading", "processing"}:
        raise HTTPException(status_code=409, detail="Job is not active")

    if not request.files:
        raise HTTPException(
            status_code=400,
            detail=(
                "Completed job must contain "
                "at least one file"
            ),
        )

    # Повторный complete не должен
    # создавать дубликаты записей files.
    for old_file in list(job.files):
        db.delete(
            old_file
        )

    for file_data in request.files:
        file_record = File(
            job_id=job.id,
            filename=file_data.filename,
            relative_path=file_data.relative_path,
            size_bytes=file_data.size_bytes,
            media_type=file_data.media_type,
            status="ready",
        )

        db.add(
            file_record
        )

    completed_at = utc_now()

    job.status = "completed"

    job.completed_at = (
        completed_at
    )

    job.expires_at = (
        completed_at
        + timedelta(
            minutes=JOB_TTL_MINUTES
        )
    )

    job.total_items = len(
        request.files
    )

    job.completed_items = len(
        request.files
    )

    job.error_message = None

    db.commit()

    return WorkerStatusResponse(
        job_code=job.job_code,
        status=job.status,
    )


# =========================================================
# Failed job
# =========================================================

@router.post(
    "/jobs/{job_code}/failed",
    response_model=WorkerStatusResponse,
)
def fail_job(
    job_code: str,
    request: WorkerFailedRequest,
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(Job).where(
            Job.job_code == job_code
        ).with_for_update()
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    if job.status == "failed":
        return WorkerStatusResponse(job_code=job.job_code, status=job.status)
    if job.status not in {"downloading", "processing"}:
        raise HTTPException(status_code=409, detail="Job is not active")

    job.status = "failed"

    job.error_message = (
        request.error_message[:4000]
    )

    db.commit()

    return WorkerStatusResponse(
        job_code=job.job_code,
        status=job.status,
    )


# =========================================================
# Expired jobs for host cleanup
# =========================================================

@router.get(
    "/jobs/expired",
    response_model=list[
        WorkerExpiredJobResponse
    ],
)
def get_expired_jobs(
    db: Session = Depends(get_db),
):
    now = utc_now()

    jobs = db.scalars(
        select(Job)
        .where(
            Job.status == "completed",
            Job.expires_at.is_not(None),
            Job.expires_at <= now,
        )
        .order_by(
            Job.expires_at
        )
        .limit(100)
    ).all()

    return [
        WorkerExpiredJobResponse(
            job_code=job.job_code
        )
        for job in jobs
    ]


# =========================================================
# Confirm filesystem cleanup
# =========================================================

@router.post(
    "/jobs/{job_code}/expired",
    response_model=WorkerStatusResponse,
)
def mark_job_expired(
    job_code: str,
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(Job).where(
            Job.job_code == job_code
        )
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    # Повторный вызов безопасен.
    if job.status == "expired":
        return WorkerStatusResponse(
            job_code=job.job_code,
            status=job.status,
        )

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only completed jobs "
                "can be expired"
            ),
        )

    if job.expires_at is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Job has no expiration date"
            ),
        )

    if job.expires_at > utc_now():
        raise HTTPException(
            status_code=409,
            detail=(
                "Job has not expired yet"
            ),
        )

    job.status = "expired"

    db.commit()

    return WorkerStatusResponse(
        job_code=job.job_code,
        status=job.status,
    )


@router.get("/jobs/active", response_model=list[WorkerStatusResponse])
def get_active_jobs(db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.status.in_(["downloading", "processing"])).order_by(Job.id)).all()
    return [WorkerStatusResponse(job_code=job.job_code, status=job.status) for job in jobs]
