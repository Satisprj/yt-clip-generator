"""Runs a job through its processing stages and records progress in the database.

New phases add stages to STAGES; the runner, progress and error handling stay the same.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.session import get_session_factory
from app.models import Job, JobStatus
from app.models.job import utcnow
from app.worker import stages
from app.worker.context import StageContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage:
    status: JobStatus
    message: str
    run: Callable[[StageContext], None]
    weight: int  # relative share of the progress bar


STAGES: tuple[Stage, ...] = (
    Stage(JobStatus.FETCHING_INFO, "Fetching video information", stages.fetch_info, 5),
    Stage(JobStatus.DOWNLOADING, "Downloading video", stages.download, 30),
    Stage(JobStatus.EXTRACTING_AUDIO, "Extracting audio", stages.extract_audio, 5),
    Stage(JobStatus.TRANSCRIBING, "Transcribing speech", stages.transcribe, 40),
)

COMPLETED_MESSAGE = (
    "Transcript ready. Highlight detection and clip generation are added in later phases."
)


def run_pipeline(job_id: uuid.UUID) -> JobStatus | None:
    """Process one job. Never raises: failures are stored on the job."""
    with get_session_factory()() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.warning("Job %s no longer exists; skipping", job_id)
            return None
        if job.status.is_terminal:
            # The broker may redeliver a task (acks_late); finished jobs are left alone.
            logger.info("Job %s already %s; skipping", job_id, job.status)
            return job.status

        total_weight = sum(s.weight for s in STAGES)
        done_weight = 0
        current: Stage | None = None
        try:
            for current in STAGES:
                start = 100 * done_weight / total_weight
                job.status = current.status
                job.stage_message = current.message
                job.progress = int(start)
                db.commit()

                ctx = StageContext(db, job, start_percent=start,
                                   span_percent=100 * current.weight / total_weight)
                current.run(ctx)
                done_weight += current.weight
                db.commit()

            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.stage_message = COMPLETED_MESSAGE
            job.completed_at = utcnow()
            db.commit()
            logger.info("Job %s completed", job_id)
            return job.status

        except AppError as exc:
            logger.warning("Job %s failed at %s: %s", job_id, _name(current), exc.message)
            return _mark_failed(db, job_id, exc.message)
        except Exception:
            logger.exception("Job %s crashed at %s", job_id, _name(current))
            return _mark_failed(
                db, job_id, f"Unexpected error while {_name(current)}. See the worker log."
            )


def _name(stage: Stage | None) -> str:
    return stage.message.lower() if stage else "starting"


def _mark_failed(db: Session, job_id: uuid.UUID, message: str) -> JobStatus:
    db.rollback()
    job = db.get(Job, job_id)
    if job is None:
        return JobStatus.FAILED
    job.status = JobStatus.FAILED
    job.error_message = message
    job.stage_message = None
    job.completed_at = utcnow()
    db.commit()
    return JobStatus.FAILED
