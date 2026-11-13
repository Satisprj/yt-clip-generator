"""Celery tasks. Pipeline tasks are added here from Phase 1 onward."""

from app.worker.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> str:
    """Health check task: verifies a worker is consuming the queue."""
    return "pong"
