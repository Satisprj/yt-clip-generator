"""Disk cleanup: old source files, old jobs, orphaned folders and temporary ZIPs.

Runs periodically in the worker (see app/worker/heartbeat.py). Settings:
- SOURCE_RETENTION_DAYS: delete the downloaded video/audio of jobs finished longer ago
  (clips are kept; reprocessing downloads the video again). 0 = keep forever.
- JOB_RETENTION_DAYS: delete finished jobs entirely (clips included). 0 = keep forever.
"""

import logging
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.db.session import get_session_factory
from app.models import Job, JobStatus
from app.services import storage

logger = logging.getLogger(__name__)

TERMINAL = (JobStatus.COMPLETED, JobStatus.FAILED)
ORPHAN_MIN_AGE = 3600     # leave very new folders alone (a job may be starting)
TEMP_ZIP_MAX_AGE = 86400  # ZIP downloads are deleted after sending; this catches leftovers


@dataclass
class CleanupReport:
    sources_removed: int = 0
    jobs_deleted: int = 0
    orphan_dirs_removed: int = 0
    temp_files_removed: int = 0
    bytes_freed: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.sources_removed or self.jobs_deleted or self.orphan_dirs_removed
                    or self.temp_files_removed)


def run_cleanup(now: datetime | None = None) -> CleanupReport:
    settings = get_settings()
    now = now or datetime.now(UTC)
    report = CleanupReport()

    with get_session_factory()() as db:
        if settings.job_retention_days > 0:
            cutoff = now - timedelta(days=settings.job_retention_days)
            for job in db.scalars(select(Job).where(Job.status.in_(TERMINAL),
                                                    Job.completed_at < cutoff)):
                size = storage.directory_size(storage.job_files(job.id).dir)
                try:
                    storage.delete_job_files(job.id)
                except ConflictError as exc:
                    report.skipped.append(f"job {job.id}: {exc.message}")
                    continue
                db.delete(job)
                db.commit()
                report.jobs_deleted += 1
                report.bytes_freed += size

        if settings.source_retention_days > 0:
            cutoff = now - timedelta(days=settings.source_retention_days)
            for job in db.scalars(select(Job).where(Job.status.in_(TERMINAL),
                                                    Job.completed_at < cutoff)):
                files = storage.job_files(job.id)
                if not (files.source_video.is_file() or files.audio.is_file()
                        or files.audio_source is not None):
                    continue
                try:
                    report.bytes_freed += storage.delete_source_files(job.id)
                    report.sources_removed += 1
                except ConflictError as exc:
                    report.skipped.append(f"job {job.id}: {exc.message}")

        known = {str(job_id) for job_id in db.scalars(select(Job.id))}

    jobs_dir = settings.jobs_dir
    if jobs_dir.is_dir():
        for folder in jobs_dir.iterdir():
            if not folder.is_dir() or folder.name in known or not _is_uuid(folder.name):
                continue
            if time.time() - folder.stat().st_mtime < ORPHAN_MIN_AGE:
                continue
            size = storage.directory_size(folder)
            shutil.rmtree(folder, ignore_errors=True)
            if not folder.exists():
                report.orphan_dirs_removed += 1
                report.bytes_freed += size

    for path in Path(tempfile.gettempdir()).glob("clipgen-*.zip"):
        try:
            if time.time() - path.stat().st_mtime > TEMP_ZIP_MAX_AGE:
                size = path.stat().st_size
                path.unlink()
                report.temp_files_removed += 1
                report.bytes_freed += size
        except OSError:
            continue

    if report.changed or report.skipped:
        logger.info(
            "Cleanup: %d source sets, %d jobs, %d orphan folders, %d temp files removed; "
            "%.1f MB freed; %d skipped", report.sources_removed, report.jobs_deleted,
            report.orphan_dirs_removed, report.temp_files_removed, report.bytes_freed / 1e6,
            len(report.skipped))
    return report


def _is_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True
