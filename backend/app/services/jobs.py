"""Job creation, lookup and listing."""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidInputError, NotFoundError, ServiceUnavailableError
from app.models import Job, JobStatus
from app.models.job import utcnow
from app.services.youtube import canonical_url, extract_video_id

logger = logging.getLogger(__name__)


def create_job(db: Session, url: str) -> Job:
    """Validate the URL, store a queued job and hand it to the worker."""
    video_id = extract_video_id(url)
    if video_id is None:
        raise InvalidInputError("Enter a valid YouTube video URL.")

    job = Job(youtube_url=canonical_url(video_id), youtube_video_id=video_id,
              stage_message="Waiting for a worker")
    db.add(job)
    db.commit()

    # Imported here so the API module graph doesn't depend on the worker at import time.
    from app.worker.tasks import process_job

    try:
        process_job.delay(str(job.id))
    except Exception as exc:
        logger.exception("Could not enqueue job %s", job.id)
        job.status = JobStatus.FAILED
        job.error_message = "The job queue is unavailable. Please try again."
        job.completed_at = utcnow()
        db.commit()
        raise ServiceUnavailableError("The job queue is unavailable. Please try again.") from exc

    logger.info("Created job %s for video %s", job.id, video_id)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return job


def list_jobs(db: Session, *, limit: int, offset: int) -> tuple[list[Job], int]:
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(
        select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
    ).all()
    return list(jobs), total
