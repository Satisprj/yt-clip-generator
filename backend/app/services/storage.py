"""File layout for job data: storage/jobs/<job_id>/..."""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import ConflictError, ServiceUnavailableError


@dataclass(frozen=True)
class JobFiles:
    dir: Path

    @property
    def source_video(self) -> Path:
        return self.dir / "source.mp4"

    @property
    def audio(self) -> Path:
        return self.dir / "audio.wav"

    @property
    def audio_source(self) -> Path | None:
        """Audio-only download (smart mode), e.g. audio_source.webm; None if absent."""
        if not self.dir.is_dir():
            return None
        found = [p for p in self.dir.glob("audio_source.*")
                 if p.suffix not in (".part", ".ytdl", ".tmp")]
        return found[0] if found else None

    @property
    def clips_dir(self) -> Path:
        return self.dir / "clips"

    @property
    def highlights(self) -> Path:
        """Raw AI responses, kept for debugging and re-ranking."""
        return self.dir / "highlights.json"


def job_files(job_id: uuid.UUID) -> JobFiles:
    return JobFiles(get_settings().jobs_dir / str(job_id))


def directory_size(path: Path) -> int:
    """Total size of all files under `path` (0 if it doesn't exist)."""
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _remove(path: Path) -> None:
    """Delete a file or folder; a file still in use (Windows) becomes a friendly error."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except PermissionError as exc:
        raise ConflictError(
            f"{path.name} is in use (maybe playing in the browser or open elsewhere). "
            "Close it and try again."
        ) from exc


def delete_job_files(job_id: uuid.UUID) -> None:
    _remove(job_files(job_id).dir)


def delete_clip_files(job_id: uuid.UUID) -> None:
    _remove(job_files(job_id).clips_dir)


def delete_source_files(job_id: uuid.UUID) -> int:
    """Delete the downloaded video, audio and partial downloads; keep clips. Returns bytes freed."""
    files = job_files(job_id)
    targets = [files.source_video, files.audio]
    if files.dir.is_dir():
        targets += [p for p in files.dir.iterdir()
                    if p.is_file() and p != files.source_video
                    and (p.name.startswith("source.") or p.name.startswith("audio_source."))]
    freed = 0
    for path in targets:
        if path.is_file():
            size = path.stat().st_size
            _remove(path)
            freed += size
    return freed


def ensure_free_space(directory: Path, required_bytes: int) -> None:
    """Raise if the disk holding `directory` has less than `required_bytes` free."""
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < required_bytes:
        raise ServiceUnavailableError(
            f"Not enough disk space: {free / 1e9:.1f} GB free, "
            f"about {required_bytes / 1e9:.1f} GB needed."
        )
