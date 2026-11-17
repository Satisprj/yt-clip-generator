"""Job creation, lookup, listing, retry, reprocessing and deletion."""

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models import Clip, Job, JobStatus, Transcript
from app.models.job import utcnow
from app.services import storage
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
        job.failed_stage = JobStatus.QUEUED.value
        job.stage_message = None
        job.completed_at = utcnow()
        db.commit()
        raise ServiceUnavailableError(QUEUE_DOWN_MESSAGE) from exc


def create_job(db: Session, url: str) -> Job:
    """Validate the URL, store a queued job and hand it to the worker."""
    video_id = extract_video_id(url)
    if video_id is None:
        raise InvalidInputError("Enter a valid YouTube video URL.")
    active = db.scalar(
        select(Job.id).where(Job.youtube_video_id == video_id,
                             Job.status.not_in([JobStatus.COMPLETED, JobStatus.FAILED]))
        .limit(1)
    )
    if active is not None:
        raise ConflictError("This video is already being processed.", code="duplicate_job",
                            details={"job_id": str(active)})

    job = Job(youtube_url=canonical_url(video_id), youtube_video_id=video_id,
              stage_message="Waiting for a worker")
    db.add(job)
    db.commit()
    _enqueue(db, job)
    logger.info("Created job %s for video %s", job.id, video_id)
    return job


def _requeue(db: Session, job: Job) -> None:
    job.status = JobStatus.QUEUED
    job.progress = 0
    job.error_message = None
    job.failed_stage = None
    job.stage_message = "Waiting for a worker"
    job.completed_at = None
    db.commit()
    _enqueue(db, job)


def _require_finished(job: Job, action: str) -> None:
    if not job.status.is_terminal:
        raise ConflictError(
            f"Can't {action} while the job is {job.status.value.replace('_', ' ')}. "
            "Wait until it has finished or failed."
        )


def retry_job(db: Session, job_id: uuid.UUID) -> Job:
    """Queue a failed job again. Finished stages are skipped by the worker."""
    job = get_job(db, job_id)
    if job.status != JobStatus.FAILED:
        raise ConflictError(f"Only failed jobs can be retried (this job is {job.status.value}).")
    _requeue(db, job)
    logger.info("Retrying job %s", job.id)
    return job


ReprocessFrom = Literal["highlights", "render"]


def reprocess_job(db: Session, job_id: uuid.UUID, start_from: ReprocessFrom) -> Job:
    """Run a finished job again from a later stage, reusing the download and transcript.

    - "highlights": discard the clips and ask Claude for new moments (then render).
    - "render": keep the chosen clips and render them again (e.g. after changing
      caption or quality settings).
    """
    job = get_job(db, job_id)
    _require_finished(job, "reprocess the job")
    if db.scalar(select(Transcript.id).where(Transcript.job_id == job.id)) is None:
        raise ConflictError("This job has no transcript yet; use Retry instead.")

    storage.delete_clip_files(job.id)
    if start_from == "highlights":
        db.execute(delete(Clip).where(Clip.job_id == job.id))
        storage.job_files(job.id).highlights.unlink(missing_ok=True)
    else:
        for clip in db.scalars(select(Clip).where(Clip.job_id == job.id)):
            clip.render_status, clip.render_error = "pending", None
            clip.video_file = clip.subtitle_file = clip.thumbnail_file = None
            clip.file_size_bytes = clip.layout = None
    _requeue(db, job)
    logger.info("Reprocessing job %s from %s", job.id, start_from)
    return job


def delete_job(db: Session, job_id: uuid.UUID) -> None:
    """Delete a finished job, its clips, transcript and all of its files."""
    job = get_job(db, job_id)
    _require_finished(job, "delete the job")
    storage.delete_job_files(job.id)  # files first: if one is locked, nothing is lost
    db.delete(job)
    db.commit()
    logger.info("Deleted job %s", job_id)


def free_source_space(db: Session, job_id: uuid.UUID) -> int:
    """Delete the downloaded video and audio of a finished job; clips are kept."""
    job = get_job(db, job_id)
    _require_finished(job, "free up space")
    freed = storage.delete_source_files(job.id)
    logger.info("Freed %.1f MB of source files for job %s", freed / 1e6, job_id)
    return freed


@dataclass(frozen=True)
class JobStats:
    clip_count: int = 0
    ready_clip_count: int = 0
    storage_bytes: int = 0
    has_source_files: bool = False


def job_stats(db: Session, jobs: list[Job]) -> dict[uuid.UUID, JobStats]:
    """Clip counts (one query for all jobs) and disk usage per job."""
    ids = [j.id for j in jobs]
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    if ids:
        rows = db.execute(
            select(Clip.job_id, func.count(),
                   func.count().filter(Clip.render_status == "ready"))
            .where(Clip.job_id.in_(ids)).group_by(Clip.job_id)
        )
        counts = {job_id: (total, ready) for job_id, total, ready in rows}
    stats = {}
    for job in jobs:
        files = storage.job_files(job.id)
        total, ready = counts.get(job.id, (0, 0))
        stats[job.id] = JobStats(total, ready, storage.directory_size(files.dir),
                                 files.source_video.is_file() or files.audio.is_file()
                                 or files.audio_source is not None)
    return stats


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


def list_clips(db: Session, job_id: uuid.UUID) -> list[Clip]:
    get_job(db, job_id)  # 404 if the job doesn't exist
    return list(db.scalars(select(Clip).where(Clip.job_id == job_id).order_by(Clip.rank)))


def list_jobs(db: Session, *, limit: int, offset: int) -> tuple[list[Job], int]:
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(
        select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
    ).all()
    return list(jobs), total
