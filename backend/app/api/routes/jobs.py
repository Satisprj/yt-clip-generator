import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.job import JobCreate, JobList, JobRead
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
