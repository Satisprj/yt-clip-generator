"""Celery application.

Run the worker (from backend/):
    celery -A app.worker.celery_app worker --loglevel=INFO --pool=solo   # Windows
    celery -A app.worker.celery_app worker --loglevel=INFO               # Linux/macOS
"""

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()

celery_app = Celery(
    "clipgen",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="clipgen",
    task_track_started=True,
    # Long video jobs: acknowledge after completion so a crashed worker's job is redelivered,
    # and fetch one task at a time so long jobs don't block others in the prefetch buffer.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=24 * 3600,
    broker_connection_retry_on_startup=True,
)


@setup_logging.connect
def _configure_worker_logging(**_: object) -> None:
    configure_logging(settings.log_level)
