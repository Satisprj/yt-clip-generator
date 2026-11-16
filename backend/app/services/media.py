"""FFmpeg / FFprobe helpers."""

import json
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]


class MediaError(ExternalServiceError):
    code = "media_error"


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    video_codec: str | None
    size_bytes: int


def _binary(configured: str, name: str) -> str:
    resolved = shutil.which(configured)
    if resolved is None:
        raise MediaError(
            f"{name} not found ('{configured}'). Install FFmpeg or set its path in .env."
        )
    return resolved


def probe(path: Path) -> MediaInfo:
    """Read duration and streams of a media file."""
    if not path.is_file():
        raise MediaError(f"Media file not found: {path.name}")
    ffprobe = _binary(get_settings().ffprobe_path, "ffprobe")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise MediaError(f"Could not read {path.name}: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    return MediaInfo(
        duration_seconds=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        video_codec=video.get("codec_name") if video else None,
        size_bytes=path.stat().st_size,
    )


def is_valid_media(path: Path, *, need_video: bool, need_audio: bool) -> bool:
    """True if `path` exists and is a readable file with the required streams."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        info = probe(path)
    except MediaError:
        return False
    return (info.duration_seconds > 0
            and (info.has_video or not need_video)
            and (info.has_audio or not need_audio))


def run_ffmpeg(
    args: list[str],
    *,
    duration_seconds: float | None = None,
    on_progress: ProgressCallback | None = None,
    timeout_seconds: float = 3600,
) -> None:
    """Run ffmpeg with machine-readable progress; raise MediaError on failure."""
    ffmpeg = _binary(get_settings().ffmpeg_path, "ffmpeg")
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    deadline = time.monotonic() + timeout_seconds

    # stderr goes to a temp file so a chatty ffmpeg can never block on a full pipe.
    with tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file)
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if time.monotonic() > deadline:
                    raise MediaError(f"FFmpeg timed out after {int(timeout_seconds)} seconds.")
                line = raw.decode("utf-8", "replace").strip()
                if on_progress and duration_seconds and line.startswith("out_time_us="):
                    value = line.partition("=")[2]
                    if value.isdigit():
                        on_progress(min(1.0, int(value) / 1e6 / duration_seconds))
            returncode = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

        if returncode != 0:
            stderr_file.seek(0)
            lines = stderr_file.read().decode("utf-8", "replace").strip().splitlines()
            logger.error("ffmpeg failed (%s): %s", returncode, "\n".join(lines[-15:]))
            last = lines[-1] if lines else "no error output"
            raise MediaError(f"FFmpeg failed (exit code {returncode}): {last[:300]}")

    if on_progress:
        on_progress(1.0)


def extract_audio(
    source: Path,
    destination: Path,
    *,
    duration_seconds: float | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Write the first audio track as 16 kHz mono 16-bit WAV (what Whisper expects)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.stem + ".partial.wav")
    run_ffmpeg(
        ["-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(partial)],
        duration_seconds=duration_seconds,
        on_progress=on_progress,
    )
    partial.replace(destination)  # only a complete file ever has the final name
    return destination
