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
    cwd: Path | None = None,
) -> None:
    """Run ffmpeg with machine-readable progress; raise MediaError on failure.

    `cwd` lets filter arguments use plain relative file names (Windows paths with drive
    letters need awkward escaping inside FFmpeg filter strings).
    """
    ffmpeg = _binary(get_settings().ffmpeg_path, "ffmpeg")
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    deadline = time.monotonic() + timeout_seconds

    # stderr goes to a temp file so a chatty ffmpeg can never block on a full pipe.
    with tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file,
                                cwd=str(cwd) if cwd else None)
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


def _input_args(source: str, headers: dict[str, str] | None, start: float) -> list[str]:
    args: list[str] = []
    if source.startswith(("http://", "https://")):
        # Survive dropped connections while reading straight from the web.
        args += ["-reconnect", "1", "-reconnect_on_network_error", "1",
                 "-reconnect_delay_max", "10", "-rw_timeout", "30000000"]
        if headers:
            args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    return args + ["-ss", f"{start:.3f}", "-i", source]


def fetch_segment(
    video: str,
    destination: Path,
    *,
    start: float,
    duration: float,
    audio: str | None = None,
    video_headers: dict[str, str] | None = None,
    audio_headers: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Copy `duration` seconds from `start` of a (remote or local) video into an MP4.

    Only the needed part is read (HTTP range requests for web URLs). The segment is
    re-encoded with a fast preset so it starts exactly at `start` (stream copying would start
    at the previous keyframe, shifting captions and crops by an unknown amount).
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.stem + ".partial.mp4")
    args = _input_args(video, video_headers, start)
    if audio:
        args += _input_args(audio, audio_headers, start)
    args += ["-t", f"{duration:.3f}", "-map", "0:v:0",
             "-map", "1:a:0" if audio else "0:a:0?",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "17",
             "-c:a", "aac", "-b:a", "192k", str(partial)]
    run_ffmpeg(args, duration_seconds=duration, on_progress=on_progress,
               timeout_seconds=max(600.0, duration * 20))
    partial.replace(destination)
    return destination


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
