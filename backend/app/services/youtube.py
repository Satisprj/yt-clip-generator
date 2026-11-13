"""YouTube URL parsing and video metadata extraction (yt-dlp, no download)."""

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, InvalidInputError

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
