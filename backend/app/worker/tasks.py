"""Celery tasks."""

import uuid

from app.worker.celery_app import celery_app
from app.worker.pipeline import run_pipeline


@celery_app.task(name="system.ping")
def ping() -> str:
    """Health check task: verifies a worker is consuming the queue."""
    return "pong"


@celery_app.task(name="jobs.process")
def process_job(job_id: str) -> str | None:
    """Run the processing pipeline for one job."""
    status = run_pipeline(uuid.UUID(job_id))
    return status.value if status else None
