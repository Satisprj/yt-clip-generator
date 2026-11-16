"""Job creation, lookup, listing and retry."""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models import Job, JobStatus, Transcript
from app.models.job import utcnow
from app.services.youtube import canonical_url, extract_video_id

logger = logging.getLogger(__name__)

QUEUE_DOWN_MESSAGE = "The job queue is unavailable. Please try again."


def _enqueue(db: Session, job: Job) -> None:
    """Send a committed job to the worker; mark it failed if the queue is down."""
    # Imported here so the API module graph doesn't depend on the worker at import time.
    from app.worker.tasks import process_job

    try:
        process_job.delay(str(job.id))
    except Exception as exc:
        logger.exception("Could not enqueue job %s", job.id)
        job.status = JobStatus.FAILED
        job.error_message = QUEUE_DOWN_MESSAGE
        job.stage_message = None
        job.completed_at = utcnow()
        db.commit()
        raise ServiceUnavailableError(QUEUE_DOWN_MESSAGE) from exc


def create_job(db: Session, url: str) -> Job:
    """Validate the URL, store a queued job and hand it to the worker."""
    video_id = extract_video_id(url)
    if video_id is None:
        raise InvalidInputError("Enter a valid YouTube video URL.")

    job = Job(youtube_url=canonical_url(video_id), youtube_video_id=video_id,
              stage_message="Waiting for a worker")
    db.add(job)
    db.commit()
    _enqueue(db, job)
    logger.info("Created job %s for video %s", job.id, video_id)
    return job


def retry_job(db: Session, job_id: uuid.UUID) -> Job:
    """Queue a failed job again. Finished stages are skipped by the worker."""
    job = get_job(db, job_id)
    if job.status != JobStatus.FAILED:
        raise ConflictError(f"Only failed jobs can be retried (this job is {job.status.value}).")
    job.status = JobStatus.QUEUED
    job.progress = 0
    job.error_message = None
    job.stage_message = "Waiting for a worker"
    job.completed_at = None
    db.commit()
    _enqueue(db, job)
    logger.info("Retrying job %s", job.id)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return job


def get_transcript(db: Session, job_id: uuid.UUID) -> Transcript:
    get_job(db, job_id)  # 404 if the job doesn't exist
    transcript = db.scalar(select(Transcript).where(Transcript.job_id == job_id))
    if transcript is None:
        raise NotFoundError("This job has no transcript yet.", code="transcript_not_ready")
    return transcript


def list_jobs(db: Session, *, limit: int, offset: int) -> tuple[list[Job], int]:
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(
        select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
    ).all()
    return list(jobs), total
