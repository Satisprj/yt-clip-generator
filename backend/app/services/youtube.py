"""YouTube URL parsing and video metadata extraction (yt-dlp, no download)."""

import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.core.config import get_settings
from app.core.exceptions import AppError, ExternalServiceError, InvalidInputError

logger = logging.getLogger(__name__)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "youtube-nocookie.com"}
_PATH_ID = re.compile(r"^/(?:shorts|embed|live|v)/([^/]+)")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def extract_video_id(url: str) -> str | None:
    """Return the 11-character video ID for common YouTube URL formats, else None.

    Mirrors frontend/src/utils/youtube.js.
    """
    try:
        parsed = urlparse(url.strip())
    except (AttributeError, ValueError):
        return None
    if parsed.scheme not in ("http", "https"):
        return None

    host = re.sub(r"^(www\.|m\.|music\.)", "", (parsed.hostname or "").lower())
    candidate: str | None = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
        elif match := _PATH_ID.match(parsed.path):
            candidate = match.group(1)

    return candidate if candidate and _VIDEO_ID.fullmatch(candidate) else None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    channel: str | None
    duration_seconds: int | None
    thumbnail_url: str | None
    is_live: bool


def ydl_base_options() -> dict[str, Any]:
    """yt-dlp options shared by metadata extraction and (later) downloads."""
    settings = get_settings()
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "logger": logging.getLogger("yt_dlp"),
    }
    ffmpeg = shutil.which(settings.ffmpeg_path)
    if ffmpeg:
        options["ffmpeg_location"] = str(Path(ffmpeg).parent)
    if settings.js_runtime_path:
        runtime = shutil.which(settings.js_runtime_path)
        if runtime:
            options["js_runtimes"] = {"node": {"path": runtime}}
        else:
            logger.warning("JS runtime '%s' not found; some YouTube formats may be missing.",
                           settings.js_runtime_path)
    if settings.ytdlp_cookies_file:
        options["cookiefile"] = str(settings.ytdlp_cookies_file)
    return options


def _clean(message: str) -> str:
    text = _ANSI.sub("", message).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s*[A-Za-z0-9_-]{11}:\s*", "", text)
    return text.splitlines()[0] if text else "unknown error"


def _translate_error(exc: Exception) -> ExternalServiceError | InvalidInputError:
    message = _clean(str(exc))
    lower = message.lower()
    if "private video" in lower:
        return InvalidInputError("This video is private.")
    if "confirm your age" in lower or "age-restricted" in lower or "inappropriate" in lower:
        return InvalidInputError(
            "This video is age-restricted. Set YTDLP_COOKIES_FILE to a logged-in cookies file."
        )
    if "not a bot" in lower or "sign in to confirm" in lower:
        return ExternalServiceError(
            "YouTube asked for a sign-in (bot check). Set YTDLP_COOKIES_FILE or try again later."
        )
    if "members-only" in lower or "join this channel" in lower:
        return InvalidInputError("This video is for channel members only.")
    if "video unavailable" in lower or "not available" in lower or "has been removed" in lower:
        return InvalidInputError("This video is unavailable.")
    return ExternalServiceError(f"YouTube error: {message}")


def fetch_video_info(url: str) -> VideoInfo:
    options = {**ydl_base_options(), "skip_download": True}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise _translate_error(exc) from exc
    except Exception as exc:  # yt-dlp can raise many internal error types
        logger.exception("Unexpected yt-dlp failure for %s", url)
        raise ExternalServiceError(f"Could not read video information: {exc}") from exc

    if not info or not info.get("id"):
        raise ExternalServiceError("YouTube returned no video information.")

    duration = info.get("duration")
    return VideoInfo(
        video_id=info["id"],
        title=(info.get("title") or "Untitled video")[:500],
        channel=(info.get("channel") or info.get("uploader") or None),
        duration_seconds=int(duration) if duration else None,
        thumbnail_url=info.get("thumbnail"),
        is_live=bool(info.get("is_live")),
    )


DownloadProgress = Callable[[float, str], None]


def _format_selector(max_height: int) -> dict[str, Any]:
    # Prefer H.264 + AAC up to max_height: decodes everywhere (FFmpeg, OpenCV, browsers).
    # Fall back to any codec, then to a single combined file.
    return {
        "format": (f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"),
        "format_sort": ["vcodec:h264", f"res:{max_height}", "acodec:m4a"],
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    }


@dataclass(frozen=True)
class Stream:
    """A direct media URL (with the HTTP headers YouTube expects) for FFmpeg to read."""

    url: str
    headers: dict[str, str]


def resolve_streams(url: str) -> tuple[Stream, Stream | None]:
    """Direct URLs of the best video (and separate audio) without downloading anything.

    URLs expire after a few hours, so resolve them right before use.
    """
    options = {**ydl_base_options(), **_format_selector(get_settings().max_download_height),
               "skip_download": True}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise _translate_error(exc) from exc
    if not info:
        raise ExternalServiceError("YouTube returned no stream information.")
    formats = info.get("requested_formats") or [info]

    def stream(f: dict[str, Any]) -> Stream:
        return Stream(f["url"], dict(f.get("http_headers") or {}))

    if len(formats) == 1:
        return stream(formats[0]), None
    video = next(f for f in formats if f.get("vcodec") not in (None, "none"))
    audio = next(f for f in formats if f is not video)
    return stream(video), stream(audio)


def download_audio(
    url: str,
    directory: Path,
    *,
    on_progress: DownloadProgress | None = None,
) -> Path:
    """Download only the audio track (a few MB per 10 minutes) as audio_source.<ext>."""
    directory.mkdir(parents=True, exist_ok=True)
    report = on_progress or (lambda fraction, message: None)

    def progress_hook(d: dict[str, Any]) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if total:
            fraction = min(0.99, int(d.get("downloaded_bytes") or 0) / total)
            report(fraction, f"Downloading audio: {fraction:.0%}")

    options = {
        **ydl_base_options(),
        # Small speech-quality audio is plenty for transcription (16 kHz mono later).
        "format": "ba[abr<=96]/ba/b",
        "outtmpl": {"default": str(directory / "audio_source.%(ext)s")},
        "progress_hooks": [progress_hook],
        "noprogress": True,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 10,
    }
    try:
        with YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise _translate_error(exc) from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("Unexpected yt-dlp audio download failure for %s", url)
        raise ExternalServiceError(f"Audio download failed: {exc}") from exc

    found = [p for p in directory.glob("audio_source.*") if p.suffix not in (".part", ".ytdl")]
    if not found:
        raise ExternalServiceError("Audio download finished but no audio file was created.")
    report(1.0, "Audio downloaded")
    return found[0]


def _estimated_size(info: dict[str, Any]) -> int | None:
    formats = info.get("requested_formats") or [info]
    sizes = [f.get("filesize") or f.get("filesize_approx") for f in formats]
    return sum(sizes) if all(sizes) else None


def download_video(
    url: str,
    destination: Path,
    *,
    on_progress: DownloadProgress | None = None,
    check_space: Callable[[int | None], None] | None = None,
) -> Path:
    """Download the video (merged to MP4) to `destination`.

    `check_space(estimated_bytes)` is called after format selection and before the
    download starts. Partial downloads are resumed if the job is retried.
    """
    settings = get_settings()
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = on_progress or (lambda fraction, message: None)
    downloaded: dict[str, int] = {}
    totals: dict[str, int] = {}

    def progress_hook(d: dict[str, Any]) -> None:
        fmt = str((d.get("info_dict") or {}).get("format_id") or d.get("filename"))
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if total:
            totals[fmt] = int(total)
        if d.get("status") == "downloading":
            downloaded[fmt] = int(d.get("downloaded_bytes") or 0)
        elif d.get("status") == "finished":
            downloaded[fmt] = totals.get(fmt, downloaded.get(fmt, 0))
        else:
            return
        expected = max(sum(totals.values()), 1)
        fraction = min(0.99, sum(downloaded.values()) / expected)
        speed = d.get("speed")
        speed_text = f" at {speed / 1e6:.1f} MB/s" if speed else ""
        report(fraction, f"Downloading video: {fraction:.0%}{speed_text}")

    def postprocessor_hook(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            name = str(d.get("postprocessor") or "")  # e.g. "FFmpegMerger"
            step = "Merging video and audio" if "Merger" in name else "Finalizing video file"
            report(0.99, step)

    options = {
        **ydl_base_options(),
        **_format_selector(settings.max_download_height),
        "outtmpl": {"default": str(destination.with_suffix("")) + ".%(ext)s"},
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "noprogress": True,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
    }
    try:
        with YoutubeDL(options) as ydl:
            # Resolve formats first (no download) so the expected size is known up front.
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ExternalServiceError("YouTube returned no video information.")
            for f in info.get("requested_formats") or [info]:
                if f.get("format_id") and (f.get("filesize") or f.get("filesize_approx")):
                    totals[str(f["format_id"])] = int(f.get("filesize") or f["filesize_approx"])
            if check_space:
                check_space(_estimated_size(info))
            ydl.process_ie_result(info, download=True)
    except DownloadError as exc:
        raise _translate_error(exc) from exc
    except AppError:
        raise  # our own errors (e.g. not enough disk space) pass through unchanged
    except Exception as exc:
        logger.exception("Unexpected yt-dlp download failure for %s", url)
        raise ExternalServiceError(f"Video download failed: {exc}") from exc

    if not destination.is_file():
        produced = sorted(p.name for p in destination.parent.glob(destination.stem + ".*"))
        raise ExternalServiceError(
            f"Download finished but {destination.name} was not created (found: {produced})."
        )
    report(1.0, "Download complete")
    return destination


def format_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def validate_for_processing(info: VideoInfo) -> None:
    """Raise InvalidInputError if the video cannot be turned into clips."""
    settings = get_settings()
    if info.is_live:
        raise InvalidInputError("Live streams can't be processed. Try again after it ends.")
    if info.duration_seconds is None:
        raise InvalidInputError("Could not determine the video's duration.")
    if info.duration_seconds > settings.max_video_duration_seconds:
        raise InvalidInputError(
            f"The video is {format_duration(info.duration_seconds)} long; the limit is "
            f"{format_duration(settings.max_video_duration_seconds)}."
        )
    if info.duration_seconds < settings.clip_min_seconds:
        raise InvalidInputError(
            f"The video is shorter than the minimum clip length ({settings.clip_min_seconds} s)."
        )
