from fastapi import FastAPI

from app import models
from app.database import (
    Base,
    engine,
)

from app.routers import (
    access,
    jobs,
    queue,
    worker,
)


Base.metadata.create_all(
    bind=engine
)


app = FastAPI(
    title="BOX4ONE",
    docs_url=None,
    redoc_url=None,
)


app.include_router(
    access.router
)

app.include_router(
    jobs.router
)

app.include_router(
    queue.router
)

app.include_router(
    worker.router
)


@app.get(
    "/health"
)
def health():
    return {
        "status": "ok",
    }
