import os
import secrets
from math import ceil

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
)

from sqlalchemy import (
    and_,
    func,
    or_,
    select,
)

from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job
from app.routers.jobs import (
    get_effective_status,
)
from app.schemas import (
    QueueJobResponse,
    QueueResponse,
)


router = APIRouter(
    prefix="/api/queue",
    tags=["queue"],
)


QUEUE_ACCESS_WORD = os.environ.get(
    "QUEUE_ACCESS_WORD",
    "",
)

PAGE_SIZE = 25


if not QUEUE_ACCESS_WORD:
    raise RuntimeError(
        "QUEUE_ACCESS_WORD is not configured"
    )


from app.security import verify_queue_access


def get_queue_position(
    db: Session,
    job: Job,
) -> int | None:
    if job.status != "queued":
        return None

    earlier_count = db.scalar(
        select(
            func.count(Job.id)
        ).where(
            Job.status == "queued",

            or_(
                Job.created_at
                < job.created_at,

                and_(
                    Job.created_at
                    == job.created_at,

                    Job.id
                    < job.id,
                ),
            ),
        )
    )

    return int(
        earlier_count or 0
    ) + 1


@router.get(
    "",
    response_model=QueueResponse,
    dependencies=[
        Depends(
            verify_queue_access
        ),
    ],
)
def get_queue(
    page: int = Query(
        default=1,
        ge=1,
    ),

    db: Session = Depends(
        get_db
    ),
):
    total = int(
        db.scalar(
            select(
                func.count(Job.id)
            )
        )
        or 0
    )

    pages = max(
        1,
        ceil(
            total / PAGE_SIZE
        ),
    )

    page = min(
        page,
        pages,
    )

    offset = (
        page - 1
    ) * PAGE_SIZE


    jobs = db.scalars(
        select(Job)
        .order_by(
            Job.created_at.desc(),
            Job.id.desc(),
        )
        .offset(
            offset
        )
        .limit(
            PAGE_SIZE
        )
    ).all()


    active = int(
        db.scalar(
            select(
                func.count(Job.id)
            ).where(
                Job.status.in_(
                    [
                        "downloading",
                        "processing",
                    ]
                )
            )
        )
        or 0
    )


    queued = int(
        db.scalar(
            select(
                func.count(Job.id)
            ).where(
                Job.status
                == "queued"
            )
        )
        or 0
    )


    response_jobs = []

    for job in jobs:
        effective_status = (
            get_effective_status(
                job
            )
        )

        queue_position = (
            get_queue_position(
                db,
                job,
            )
            if effective_status
            == "queued"
            else None
        )

        response_jobs.append(
            QueueJobResponse(
                job_code=job.job_code,
                status=effective_status,
                media_type=job.media_type,
                quality=job.quality,
                created_at=job.created_at,
                total_items=job.total_items,
                completed_items=job.completed_items,
                queue_position=queue_position,
            )
        )


    return QueueResponse(
        page=page,
        pages=pages,
        page_size=PAGE_SIZE,
        total=total,
        active=active,
        queued=queued,
        jobs=response_jobs,
    )
