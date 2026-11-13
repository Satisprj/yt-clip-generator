"""Pipeline stage implementations. Each stage updates the job in place.

Stages run inside an open DB session; the pipeline runner commits after each one.
"""

import logging

from sqlalchemy.orm import Session

from app.models import Job
from app.services import youtube

logger = logging.getLogger(__name__)


def fetch_info(db: Session, job: Job) -> None:
    info = youtube.fetch_video_info(job.youtube_url)
    job.title = info.title
    job.channel = info.channel
    job.duration_seconds = info.duration_seconds
    job.thumbnail_url = info.thumbnail_url
    # Commit before validating, so a rejected video (too long, live, ...) still shows its
    # details: the failure handler rolls back anything uncommitted.
    db.commit()
    youtube.validate_for_processing(info)
    logger.info("Job %s: '%s' (%ss)", job.id, info.title, info.duration_seconds)
