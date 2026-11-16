"""Recover jobs that were left mid-processing when the worker stopped.

If the worker process is killed (closed window, crash, PC restart), the job it was running
stays in a "running" status forever. When a worker starts, those jobs are marked failed
with a clear message; Retry resumes them (stages reuse finished work, yt-dlp resumes
partial downloads).

Assumes a single worker (the setup this project uses on Windows with --pool=solo). With
several workers, a starting worker would also flag jobs that another worker is running.
"""

import logging

from sqlalchemy import update

from app.db.session import get_session_factory
from app.models import Job, JobStatus
from app.models.job import utcnow

logger = logging.getLogger(__name__)

RUNNING_STATUSES = tuple(
    s for s in JobStatus if not s.is_terminal and s is not JobStatus.QUEUED
)
INTERRUPTED_MESSAGE = (
    "Processing was interrupted because the worker stopped. Click Retry to continue "
    "from where it stopped."
)


def fail_interrupted_jobs() -> int:
    """Mark jobs stuck in a running status as failed. Returns how many were updated."""
    with get_session_factory()() as db:
        result = db.execute(
            update(Job)
            .where(Job.status.in_(RUNNING_STATUSES))
            .values(
                status=JobStatus.FAILED,
                error_message=INTERRUPTED_MESSAGE,
                stage_message=None,
                completed_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db.commit()
        count = result.rowcount or 0
    if count:
        logger.warning("Marked %d interrupted job(s) as failed; they can be retried.", count)
    return count
