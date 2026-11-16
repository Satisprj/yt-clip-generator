"""Export all rendered clips of a job as one ZIP file."""

import re
import tempfile
import zipfile
from pathlib import Path

from app.core.exceptions import NotFoundError
from app.models import Clip, Job
from app.services import storage


def _slug(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    return re.sub(r"[\s_]+", "-", slug)[:limit].strip("-") or "clip"


def clip_basename(clip: Clip) -> str:
    return f"{clip.rank:02d}-{_slug(clip.title)}"


def captions_text(job: Job, clips: list[Clip]) -> str:
    """Titles, descriptions and hashtags for every clip, ready to paste when posting."""
    lines = [f"Clips from: {job.title or job.youtube_url}", f"Source: {job.youtube_url}", ""]
    for clip in clips:
        lines += [
            f"=== {clip_basename(clip)}.mp4 ({clip.duration_seconds:.0f} s, "
            f"score {clip.viral_score:.1f}, {clip.category}) ===",
            clip.title,
            "",
            clip.description,
            "",
            " ".join(clip.hashtags),
            "",
        ]
    return "\n".join(lines)


def build_clips_zip(job: Job, clips: list[Clip]) -> tuple[Path, str]:
    """Write a ZIP of the ready clips to a temp file. Returns (path, download name).

    The caller deletes the file after sending it.
    """
    ready = [c for c in clips if c.render_status == "ready" and c.video_file]
    if not ready:
        raise NotFoundError("This job has no rendered clips to download.", code="no_clips")
    clips_dir = storage.job_files(job.id).clips_dir

    # Reserve a temp file name (closed right away so ZipFile can reopen it on Windows).
    with tempfile.NamedTemporaryFile(prefix="clipgen-", suffix=".zip", delete=False) as handle:
        path = Path(handle.name)
    try:
        # MP4 is already compressed: store it as-is (fast); deflate the small text files.
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
            for clip in ready:
                base = clip_basename(clip)
                zf.write(clips_dir / clip.video_file, f"{base}.mp4")
                if clip.subtitle_file and (clips_dir / clip.subtitle_file).is_file():
                    zf.write(clips_dir / clip.subtitle_file, f"{base}.srt",
                             compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("captions.txt", captions_text(job, ready),
                        compress_type=zipfile.ZIP_DEFLATED)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, f"{_slug(job.title or job.youtube_video_id)}-clips.zip"
