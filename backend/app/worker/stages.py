"""Pipeline stage implementations.

Each stage receives a StageContext, updates the job in place and may report progress.
Stages are idempotent: if their output already exists (e.g. after a retry), they skip
the work, so a failed job resumes where it stopped.
"""

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.exceptions import AppError, ExternalServiceError, InvalidInputError
from app.models import Clip, Transcript
from app.services import highlights, media, storage, transcription, youtube
from app.services import render as render_service
from app.services.llm import ClaudeCliClient
from app.services.sentences import Sentence, Word, split_sentences
from app.worker.context import StageContext

logger = logging.getLogger(__name__)


def fetch_info(ctx: StageContext) -> None:
    job = ctx.job
    if job.title and job.duration_seconds:
        # Retry/reprocess: details are known, so skip the slow YouTube lookup but still
        # apply the current limits (e.g. a changed MAX_VIDEO_DURATION_SECONDS).
        youtube.validate_for_processing(youtube.VideoInfo(
            job.youtube_video_id, job.title, job.channel, job.duration_seconds,
            job.thumbnail_url, is_live=False))
        ctx.report(1.0, "Using the saved video information")
        return
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

    if get_settings().download_mode == "smart":
        # Audio first: transcription and highlight detection only need sound. The video for
        # the chosen clips is fetched later, segment by segment, in the render stage.
        audio_source = files.audio_source
        if media.is_valid_media(files.audio, need_video=False, need_audio=True) or (
                audio_source is not None
                and media.is_valid_media(audio_source, need_video=False, need_audio=True)):
            ctx.report(1.0, "Using the previously downloaded audio")
            return
        storage.ensure_free_space(files.dir, get_settings().min_free_disk_mb * 1024 * 1024)
        source = youtube.download_audio(ctx.job.youtube_url, files.dir, on_progress=ctx.report)
        if not media.probe(source).has_audio:
            raise InvalidInputError("The video has no audio track, so it can't be transcribed.")
        logger.info("Job %s: downloaded audio only (%.1f MB)", ctx.job.id,
                    source.stat().st_size / 1e6)
        return

    _download_full_video(ctx, files)


def _download_full_video(ctx: StageContext, files: storage.JobFiles) -> None:
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


def analyze(ctx: StageContext) -> None:
    job = ctx.job
    if ctx.db.scalar(select(Clip.id).where(Clip.job_id == job.id).limit(1)) is not None:
        ctx.report(1.0, "Using the previously selected highlights")
        return

    transcript = ctx.db.scalar(select(Transcript).where(Transcript.job_id == job.id))
    if transcript is None:
        raise InvalidInputError("This job has no transcript to analyze.")
    sentences = [Sentence(**s) for s in transcript.sentences]
    video = highlights.VideoContext(
        title=job.title or "Untitled video",
        channel=job.channel,
        duration_seconds=float(job.duration_seconds or transcript.duration_seconds),
        language=transcript.language,
    )

    result = highlights.detect_highlights(ClaudeCliClient(), video, sentences,
                                          on_step=ctx.report)

    files = storage.job_files(job.id)
    files.dir.mkdir(parents=True, exist_ok=True)
    files.highlights.write_text(json.dumps(
        {"candidates": result.raw_candidates, "metadata": result.raw_metadata},
        ensure_ascii=False, indent=2), encoding="utf-8")

    for clip in result.clips:
        ctx.db.add(Clip(
            job_id=job.id,
            rank=clip.rank,
            start_seconds=clip.window.start,
            end_seconds=clip.window.end,
            duration_seconds=round(clip.window.duration, 3),
            first_sentence=clip.window.first_sentence,
            last_sentence=clip.window.last_sentence,
            transcript_text=clip.text,
            category=clip.candidate.category,
            viral_score=clip.viral_score,
            scores=clip.candidate.scores,
            hook=clip.candidate.hook,
            reason=clip.candidate.reason,
            title=clip.title,
            description=clip.description,
            hashtags=clip.hashtags,
        ))
    ctx.db.commit()
    logger.info("Job %s: %d moments suggested, %d clips selected", job.id,
                result.candidates_found, len(result.clips))


def _clip_files_exist(clips_dir, clip: Clip) -> bool:
    return all(f and (clips_dir / f).is_file()
               for f in (clip.video_file, clip.subtitle_file, clip.thumbnail_file))


def clip_words(transcript: Transcript, clip: Clip) -> list[Word]:
    """Transcript words inside the clip, with times relative to the clip start."""
    sentences = transcript.sentences
    first = sentences[clip.first_sentence]["first_word"]
    last = sentences[clip.last_sentence]["last_word"]
    return [
        Word(start=round(w["start"] - clip.start_seconds, 3),
             end=round(w["end"] - clip.start_seconds, 3), text=w["text"])
        for w in transcript.words[first:last + 1]
    ]


def render(ctx: StageContext) -> None:
    job = ctx.job
    clips = list(ctx.db.scalars(select(Clip).where(Clip.job_id == job.id).order_by(Clip.rank)))
    if not clips:
        ctx.report(1.0, "No clips to render")
        return

    files = storage.job_files(job.id)
    todo = [c for c in clips
            if not (c.render_status == "ready" and _clip_files_exist(files.clips_dir, c))]
    if not todo:
        ctx.report(1.0, "Using the previously rendered clips")
        return

    transcript = ctx.db.scalar(select(Transcript).where(Transcript.job_id == job.id))
    if transcript is None:
        raise InvalidInputError("This job has no transcript, so captions can't be made.")

    sources = _ClipSources(ctx, files, clips)
    for i, clip in enumerate(todo):
        label = f"clip {i + 1} of {len(todo)}"

        def progress(fraction: float, message: str, i: int = i, label: str = label) -> None:
            ctx.report((i + fraction) / len(todo), f"Rendering {label}: {message.lower()}")

        name = f"{clip.rank:02d}"
        segment: Path | None = None
        try:
            source, offset, segment = sources.for_clip(clip, name, progress)
            result = render_service.render_clip(
                source, start=clip.start_seconds - offset, end=clip.end_seconds - offset,
                words=clip_words(transcript, clip), out_dir=files.clips_dir, name=name,
                language=transcript.language, on_progress=progress,
            )
        except AppError as exc:
            logger.warning("Job %s: rendering clip #%d failed: %s", job.id, clip.rank,
                           exc.message)
            clip.render_status, clip.render_error = "failed", exc.message
            ctx.db.commit()
            continue
        finally:
            if segment is not None:
                segment.unlink(missing_ok=True)  # segments are only needed for rendering
        clip.render_status, clip.render_error = "ready", None
        clip.layout = result.layout
        clip.video_file = result.video.name
        clip.subtitle_file = result.subtitles.name
        clip.thumbnail_file = result.thumbnail.name
        clip.file_size_bytes = result.video.stat().st_size
        ctx.db.commit()

    failed = [c for c in clips if c.render_status != "ready"]
    if len(failed) == len(clips):
        raise ExternalServiceError(f"Rendering failed for every clip: {failed[0].render_error}")


SEGMENT_PAD = 1.0  # seconds fetched before/after a clip (room for the reframe analysis)


class _ClipSources:
    """Where each clip's video comes from: the full download, or a fetched segment.

    Smart mode fetches only the seconds each clip needs, straight from YouTube. It switches
    to one full download when the clips cover a large part of the video (cheaper overall),
    or when fetching a segment fails.
    """

    def __init__(self, ctx: StageContext, files: storage.JobFiles, clips: list[Clip]) -> None:
        self.ctx, self.files = ctx, files
        self.streams: tuple[youtube.Stream, youtube.Stream | None] | None = None
        settings = get_settings()
        video_seconds = float(ctx.job.duration_seconds or 0) or 1.0
        covered = sum(c.duration_seconds + 2 * SEGMENT_PAD for c in clips)
        self.use_full = (
            settings.download_mode != "smart"
            or media.is_valid_media(files.source_video, need_video=True, need_audio=True)
            or covered / video_seconds > settings.segment_max_fraction
        )

    def _ensure_full(self, progress) -> None:
        if not media.is_valid_media(self.files.source_video, need_video=True, need_audio=True):
            progress(0.0, "Downloading the video")
            _download_full_video(self.ctx, self.files)
        self.use_full = True

    def for_clip(self, clip: Clip, name: str, progress) -> tuple[Path, float, Path | None]:
        """(source file, its start time in the original video, temp segment to delete)."""
        if self.use_full:
            self._ensure_full(progress)
            return self.files.source_video, 0.0, None
        start = max(0.0, clip.start_seconds - SEGMENT_PAD)
        duration = clip.end_seconds + SEGMENT_PAD - start
        segment = self.files.clips_dir / f"{name}.source.mp4"
        try:
            if self.streams is None:
                self.streams = youtube.resolve_streams(self.ctx.job.youtube_url)
            video, audio = self.streams
            progress(0.0, "Fetching this clip's video")
            media.fetch_segment(
                video.url, segment, start=start, duration=duration,
                audio=audio.url if audio else None, video_headers=video.headers,
                audio_headers=audio.headers if audio else None,
                on_progress=lambda f: progress(0.15 * f, f"Fetching this clip's video: {f:.0%}"))
        except AppError as exc:
            logger.warning("Job %s: fetching a segment failed (%s); downloading the full video",
                           self.ctx.job.id, exc.message)
            segment.unlink(missing_ok=True)
            self._ensure_full(progress)
            return self.files.source_video, 0.0, None
        return segment, start, segment


def completion_message(db, job) -> str:
    """Summary shown when a job finishes."""
    clips = list(db.scalars(select(Clip).where(Clip.job_id == job.id)))
    if not clips:
        return ("No moments in this video work as standalone "
                f"{get_settings().clip_min_seconds}-{get_settings().clip_max_seconds} s clips.")
    ready = sum(c.render_status == "ready" for c in clips)
    message = f"{ready} {'clip' if ready == 1 else 'clips'} ready to preview and download."
    if ready < len(clips):
        message += f" {len(clips) - ready} could not be rendered; Retry to try again."
    return message


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
    source = files.source_video if files.source_video.is_file() else files.audio_source
    if source is None:
        raise InvalidInputError("Nothing to extract audio from: the download is missing. Retry.")
    media.extract_audio(
        source,
        files.audio,
        duration_seconds=ctx.job.duration_seconds,
        on_progress=lambda f: ctx.report(f, f"Extracting audio: {f:.0%}"),
    )
