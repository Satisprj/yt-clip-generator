"""Pipeline stage implementations.

Each stage receives a StageContext, updates the job in place and may report progress.
Stages are idempotent: if their output already exists (e.g. after a retry), they skip
the work, so a failed job resumes where it stopped.
"""

import logging

from app.core.config import get_settings
from app.core.exceptions import InvalidInputError
from app.services import media, storage, youtube
from app.worker.context import StageContext

logger = logging.getLogger(__name__)


def fetch_info(ctx: StageContext) -> None:
    job = ctx.job
    info = youtube.fetch_video_info(job.youtube_url)
    job.title = info.title
    job.channel = info.channel
    job.duration_seconds = info.duration_seconds
    job.thumbnail_url = info.thumbnail_url
    # Commit before validating, so a rejected video (too long, live, ...) still shows its
    # details: the failure handler rolls back anything uncommitted.
    ctx.db.commit()
    youtube.validate_for_processing(info)
    logger.info("Job %s: '%s' (%ss)", job.id, info.title, info.duration_seconds)


def download(ctx: StageContext) -> None:
    files = storage.job_files(ctx.job.id)
    if media.is_valid_media(files.source_video, need_video=True, need_audio=True):
        ctx.report(1.0, "Using the previously downloaded video")
        return

    reserve = get_settings().min_free_disk_mb * 1024 * 1024

    def check_space(estimated_bytes: int | None) -> None:
        # Merging needs the parts plus the output, so allow twice the download size.
        storage.ensure_free_space(files.dir, reserve + 2 * (estimated_bytes or 0))

    youtube.download_video(
        ctx.job.youtube_url, files.source_video, on_progress=ctx.report, check_space=check_space
    )

    info = media.probe(files.source_video)
    if not info.has_video:
        raise InvalidInputError("The downloaded file has no video track.")
    if not info.has_audio:
        raise InvalidInputError("The video has no audio track, so it can't be transcribed.")
    logger.info("Job %s: downloaded %dx%s %s, %.1f MB", ctx.job.id, info.width or 0,
                info.height, info.video_codec, info.size_bytes / 1e6)


def extract_audio(ctx: StageContext) -> None:
    files = storage.job_files(ctx.job.id)
    if media.is_valid_media(files.audio, need_video=False, need_audio=True):
        ctx.report(1.0, "Using the previously extracted audio")
        return
    media.extract_audio(
        files.source_video,
        files.audio,
        duration_seconds=ctx.job.duration_seconds,
        on_progress=lambda f: ctx.report(f, f"Extracting audio: {f:.0%}"),
    )
