import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.clip import ClipRead
from app.schemas.job import FreeSpaceResult, JobCreate, JobList, JobRead, ReprocessRequest
from app.schemas.transcript import TranscriptRead
from app.services import export, storage
from app.services import jobs as job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])

DbSession = Annotated[Session, Depends(get_db)]


def _read(db: Session, job) -> JobRead:
    return JobRead.build(job, job_service.job_stats(db, [job])[job.id])


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: DbSession) -> JobRead:
    """Submit a YouTube URL for processing."""
    return _read(db, job_service.create_job(db, payload.url))


@router.get("", response_model=JobList)
def list_jobs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobList:
    """Processing history, newest first."""
    items, total = job_service.list_jobs(db, limit=limit, offset=offset)
    stats = job_service.job_stats(db, items)
    return JobList(
        items=[JobRead.build(j, stats[j.id]) for j in items], total=total, limit=limit,
        offset=offset, storage_bytes_total=storage.directory_size(get_settings().jobs_dir),
    )


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: uuid.UUID, db: DbSession) -> JobRead:
    return _read(db, job_service.get_job(db, job_id))


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: uuid.UUID, db: DbSession) -> Response:
    """Delete a finished job with its clips, transcript and files."""
    job_service.delete_job(db, job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/reprocess", response_model=JobRead)
def reprocess_job(job_id: uuid.UUID, payload: ReprocessRequest, db: DbSession) -> JobRead:
    """Run a finished job again from highlight detection or rendering."""
    return _read(db, job_service.reprocess_job(db, job_id, payload.start_from))


@router.post("/{job_id}/free-space", response_model=FreeSpaceResult)
def free_space(job_id: uuid.UUID, db: DbSession) -> FreeSpaceResult:
    """Delete the downloaded video and audio; rendered clips are kept."""
    freed = job_service.free_source_space(db, job_id)
    return FreeSpaceResult(freed_bytes=freed, job=_read(db, job_service.get_job(db, job_id)))


@router.get("/{job_id}/clips.zip")
def download_clips_zip(job_id: uuid.UUID, db: DbSession) -> FileResponse:
    """All rendered clips with their subtitles and a captions.txt, as one ZIP."""
    job = job_service.get_job(db, job_id)
    path, name = export.build_clips_zip(job, job_service.list_clips(db, job_id))
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.get("/{job_id}/transcript", response_model=TranscriptRead,
            response_model_exclude_none=True)
def get_transcript(
    job_id: uuid.UUID,
    db: DbSession,
    include_words: Annotated[bool, Query(description="Also return every word")] = False,
) -> TranscriptRead:
    t = job_service.get_transcript(db, job_id)
    return TranscriptRead(
        job_id=t.job_id, language=t.language, language_probability=t.language_probability,
        duration_seconds=t.duration_seconds, model=t.model, word_count=t.word_count,
        sentence_count=t.sentence_count, sentences=t.sentences,
        words=t.words if include_words else None, created_at=t.created_at,
    )


@router.get("/{job_id}/clips", response_model=list[ClipRead])
def list_clips(job_id: uuid.UUID, db: DbSession) -> list[ClipRead]:
    """Selected highlights, best first. Empty until the analysis stage has finished."""
    return [ClipRead.model_validate(c) for c in job_service.list_clips(db, job_id)]


@router.post("/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: uuid.UUID, db: DbSession) -> JobRead:
    """Re-queue a failed job; work already finished (e.g. the download) is reused."""
    return _read(db, job_service.retry_job(db, job_id))
