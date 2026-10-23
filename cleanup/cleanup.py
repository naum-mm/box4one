#!/usr/bin/env python3

import json
import logging
import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path


# =========================================================
# Configuration
# =========================================================

API_URL = os.environ["API_URL"].rstrip("/")

WORKER_API_TOKEN = os.environ[
    "WORKER_API_TOKEN"
]

JOBS_DIR = Path(
    os.environ["JOBS_DIR"]
)

JOB_CODE_PATTERN = re.compile(
    r"^[a-z0-9]{10}$"
)


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s UTC "
        "[%(levelname)s] "
        "%(message)s"
    ),
)


# =========================================================
# Backend API
# =========================================================

def api_request(
    path,
    method="GET",
    payload=None,
):
    headers = {
        "X-Worker-Token": (
            WORKER_API_TOKEN
        ),
    }

    data = None

    if payload is not None:
        headers["Content-Type"] = (
            "application/json"
        )

        data = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

    request = urllib.request.Request(
        f"{API_URL}{path}",
        method=method,
        headers=headers,
        data=data,
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        body = response.read()

        if not body:
            return None

        return json.loads(
            body.decode("utf-8")
        )


def get_expired_jobs():
    result = api_request(
        "/api/worker/jobs/expired",
        method="GET",
    )

    if not isinstance(
        result,
        list,
    ):
        return []

    return result


def mark_expired(
    job_code,
):
    return api_request(
        (
            "/api/worker/jobs/"
            f"{job_code}/expired"
        ),
        method="POST",
    )


# =========================================================
# Filesystem safety
# =========================================================

def get_job_directory(
    job_code,
):
    if not JOB_CODE_PATTERN.fullmatch(
        job_code
    ):
        raise RuntimeError(
            f"Invalid job code: {job_code}"
        )

    base = JOBS_DIR.resolve()

    job_dir = (
        JOBS_DIR
        / job_code
    ).resolve()

    if job_dir.parent != base:
        raise RuntimeError(
            "Unsafe job directory path"
        )

    return job_dir


def remove_job_directory(
    job_code,
):
    job_dir = get_job_directory(
        job_code
    )

    if not job_dir.exists():
        logging.info(
            "Directory already absent: %s",
            job_dir,
        )

        return

    if not job_dir.is_dir():
        raise RuntimeError(
            f"Job path is not a directory: "
            f"{job_dir}"
        )

    logging.info(
        "Removing directory: %s",
        job_dir,
    )

    shutil.rmtree(
        job_dir
    )

    if job_dir.exists():
        raise RuntimeError(
            f"Directory still exists after "
            f"deletion: {job_dir}"
        )


# =========================================================
# Cleanup
# =========================================================

def process_job(
    item,
):
    job_code = item.get(
        "job_code"
    )

    if not isinstance(
        job_code,
        str,
    ):
        raise RuntimeError(
            "Backend returned invalid job code"
        )

    logging.info(
        "Cleaning expired job %s",
        job_code,
    )

    # First remove filesystem data.
    #
    # Only after successful removal do we mark
    # the database record as expired.
    remove_job_directory(
        job_code
    )

    response = mark_expired(
        job_code
    )

    if not response:
        raise RuntimeError(
            "Backend did not confirm expiration"
        )

    if response.get(
        "status"
    ) != "expired":
        raise RuntimeError(
            "Unexpected backend status: "
            f"{response.get('status')}"
        )

    logging.info(
        "Job %s marked expired",
        job_code,
    )


def main():
    logging.info(
        "Cleanup started"
    )

    logging.info(
        "Jobs directory: %s",
        JOBS_DIR,
    )

    try:
        jobs = get_expired_jobs()

    except Exception:
        logging.exception(
            "Unable to get expired jobs"
        )

        raise SystemExit(1)

    if not jobs:
        logging.info(
            "No expired jobs"
        )

        return

    logging.info(
        "Found %d expired job(s)",
        len(jobs),
    )

    errors = 0

    for item in jobs:
        try:
            process_job(
                item
            )

        except Exception:
            errors += 1

            logging.exception(
                "Failed to cleanup job"
            )

    logging.info(
        "Cleanup finished: "
        "processed=%d errors=%d",
        len(jobs),
        errors,
    )

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
