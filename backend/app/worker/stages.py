"""Pipeline stage implementations.

Each stage receives a StageContext, updates the job in place and may report progress.
Stages are idempotent: if their output already exists (e.g. after a retry), they skip
the work, so a failed job resumes where it stopped.
"""

import logging
import time
from dataclasses import asdict

from sqlalchemy import select

from app.core.config import get_settings
from app.core.exceptions import InvalidInputError
from app.models import Transcript
from app.services import media, storage, transcription, youtube
from app.services.sentences import split_sentences
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


def transcribe(ctx: StageContext) -> None:
    job = ctx.job
    if ctx.db.scalar(select(Transcript.id).where(Transcript.job_id == job.id)) is not None:
        ctx.report(1.0, "Using the existing transcript")
        return

    files = storage.job_files(job.id)
    ctx.report(0.0, "Loading the speech model (the first run downloads it)")
    transcription.get_model()

    started = time.monotonic()

    def progress(fraction: float) -> None:
        elapsed = time.monotonic() - started
        eta = ""
        if 0.05 <= fraction < 1:
            remaining = elapsed * (1 - fraction) / fraction
            eta = f", about {_human_duration(remaining)} left"
        ctx.report(fraction, f"Transcribing: {fraction:.0%}{eta}")

    result = transcription.transcribe(
        files.audio, duration_seconds=job.duration_seconds, on_progress=progress
    )
    if not result.words:
        raise InvalidInputError("No speech was detected in this video, so it can't be clipped.")

    sentences = split_sentences(result.words)
    ctx.db.add(Transcript(
        job_id=job.id,
        language=result.language,
        language_probability=result.language_probability,
        duration_seconds=result.duration_seconds,
        model=result.model,
        word_count=len(result.words),
        sentence_count=len(sentences),
        words=[asdict(w) for w in result.words],
        sentences=[s.to_dict() for s in sentences],
    ))
    ctx.db.commit()
    logger.info("Job %s: %d words, %d sentences, language=%s", job.id, len(result.words),
                len(sentences), result.language)


def _human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{max(seconds, 1)} s"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


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
