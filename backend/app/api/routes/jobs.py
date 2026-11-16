import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.job import JobCreate, JobList, JobRead
from app.schemas.transcript import TranscriptRead
from app.services import jobs as job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: DbSession) -> JobRead:
    """Submit a YouTube URL for processing."""
    return JobRead.model_validate(job_service.create_job(db, payload.url))


@router.get("", response_model=JobList)
def list_jobs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobList:
    """Processing history, newest first."""
    items, total = job_service.list_jobs(db, limit=limit, offset=offset)
    return JobList(
        items=[JobRead.model_validate(j) for j in items], total=total, limit=limit, offset=offset
    )


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: uuid.UUID, db: DbSession) -> JobRead:
    return JobRead.model_validate(job_service.get_job(db, job_id))


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


@router.post("/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: uuid.UUID, db: DbSession) -> JobRead:
    """Re-queue a failed job; work already finished (e.g. the download) is reused."""
    return JobRead.model_validate(job_service.retry_job(db, job_id))
