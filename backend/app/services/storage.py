"""File layout for job data: storage/jobs/<job_id>/..."""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import ServiceUnavailableError


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
    def highlights(self) -> Path:
        """Raw AI responses, kept for debugging and re-ranking."""
        return self.dir / "highlights.json"


def job_files(job_id: uuid.UUID) -> JobFiles:
    return JobFiles(get_settings().jobs_dir / str(job_id))


def ensure_free_space(directory: Path, required_bytes: int) -> None:
    """Raise if the disk holding `directory` has less than `required_bytes` free."""
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < required_bytes:
        raise ServiceUnavailableError(
            f"Not enough disk space: {free / 1e9:.1f} GB free, "
            f"about {required_bytes / 1e9:.1f} GB needed."
        )
